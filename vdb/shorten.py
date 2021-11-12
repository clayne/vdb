#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import vdb
import vdb.color
import vdb.config
import vdb.subcommands


import re
import gdb
import os
import traceback
import time

from collections.abc import Iterable


vdb.enabled_modules.append("shorten")

color_shorten = vdb.config.parameter("vdb-shorten-colors-templates", "#f60", gdb_type = vdb.config.PARAM_COLOUR)
fold_ellipsis = vdb.config.parameter("vdb-shorten-fold-ellipsis", "…" )
recursion_limit = vdb.config.parameter("vdb-shorten-recursion-limit",3)
verbosity = vdb.config.parameter("vdb-shorten-verbosity",1)

debug = vdb.config.parameter("vdb-shorten-debug",False)
cache = vdb.config.parameter("vdb-shorten-cache",True)
lazy  = vdb.config.parameter("vdb-shorten-lazye-load-typedefs",False)


def log(fmt, *more ):
    try:
        print(fmt.format(*more))
    except:
        print(fmt)

def indent( i, fmt, *more ):
    log("  " * i + fmt, *more )

def prefix( i, msg, end = None ):
    print(" " * i + msg, end = end )


class namespace:

	def __init__(self,name,parent=None):
		self.name = name
		self.parent = parent

	def __str__(self):
		ret = self.name
		if( self.parent ):
			ret = self.parent.__str__() + "::" + ret
		return ret

class type_or_function:

    next_id = 0

    def __init__(self):
        self.namespace=None
#        self.name="{{placeholder for name}}"
        self.name=None
        self.template_parameters=None
        self.subobject=None
        self.parameters=None
        self.shortens = {}
        self.suppress_templates = False
        self.suppress_paramters = False
        self.id = type_or_function.next_id
        type_or_function.next_id += 1
        self.type = None
        self.tail = ""
        self.cached_string = None

    def to_dot( self, g ):
        n = g.node(f"{self.name}.{self.id}")
        n["id"] = self.id
        n["name"] = self.name
        n["type"] = self.type
        if( self.namespace is None ):
            n["ns"] = "None"
        else:
            n["ns"] = self.namespace
        if( self.subobject ):
            sn=self.subobject.to_dot(g)
            n.edge(sn.name)
        n["subobject"] = self.subobject
        if( self.template_parameters is not None ):
            n["tparam"] = len(self.template_parameters)
            for tp in self.template_parameters:
                tn = tp.to_dot(g)
                n["tparam"].tds[1]["port"] = "tparam"
                n.edge( tn.name, srcport = "tparam" )
        else:
            n["tparam"] = "None"

        if( self.parameters is not None ):
            n["param"] = len(self.parameters)
            for pp in self.parameters:
                pb = pp.to_dot(g)
                n["param"].tds[1]["port"] = "param"
                n.edge( pb.name, srcport = "param")
        else:
            n["param"] = None

        return n

    def set_type( self, nt ):
#        print(f"{self.type}[{self.id}] => {nt}")
        self.type = nt

#    def add_tail( self, subob ):
#        print("subob.id = '%s'" % (subob.id,) )
#        if( self.subobject ):
#            self.subobject.add_tail(subob)
#        else:
#            self.subobject = subob

    def shorten( self, name ):
        for old,new in self.shortens.items():
            name = name.replace(old,new)
        return name

    def add_ns( self, ns ):
        if( len(ns) > 0 ):
            ns=namespace(ns)
            ns.parent = self.namespace
            self.namespace = ns

    def add_param( self, par ):
        if( self.parameters is None ):
            self.parameters = []
        if( par is not None ):
#            par.dump()
            self.parameters.append(par)
#        self.dump()

    def add_template( self, tpar ):
        if( self.template_parameters is None ):
            self.template_parameters = []
        if( tpar.name is None or len(tpar.name) > 0 ):
            self.template_parameters.append(tpar)
#        print("self.id = '%s'" % (self.id,) )
#        print("self.template_parameters = '%s'" % (self.template_parameters,) )
#        print("tpar = '%s'" % (tpar,) )


    def dump( self, level = 0 ):
        indent(level,"id : " + str(self.id))
        indent(level,"ns : " + str(self.namespace))
        indent(level,"name : " + str(self.name))
        indent(level,"type : " + str(self.type))
        if( self.template_parameters is not None ):
            indent(level,"template parameters[{}]".format(len(self.template_parameters)))
            for t in self.template_parameters:
                t.dump(level+1)
        else:
            indent(level,"template parameters = None")
        if( self.parameters ):
            indent(level,"parameters[{}]".format(len(self.parameters)))
        else:
            indent(level,"parameters = None")
        if( self.subobject is not None ):
            indent(level,"Subobject")
            self.subobject.dump(level+1)


    def fold_templates( self, shortlist ):
        self.cached_string = None
        if( isinstance(shortlist,Iterable) and not isinstance(shortlist,str) ):
            for s in shortlist:
                self.fold_templates(s)
        else:
            if( self.subobject is not None ):
                self.subobject.fold_templates(shortlist)
            if( self.template_parameters is not None and len(self.template_parameters ) > 0):
                for tp in self.template_parameters:
                    tp.fold_templates(shortlist)
            if( self.parameters is not None and len(self.parameters) ):
                for p in self.parameters:
                    p.fold_templates(shortlist)
            if( self.name == shortlist ):
                self.suppress_templates = True
#                print(f"SET {self.id}:{self.name} => {self.suppress_templates}")

    def add_shorten( self, fro, to = None ):
        self.cached_string = None
        if( to is None ):
            for f,t in fro.items():
                self.add_shorten(f,t)
        else:
            if( self.subobject is not None ):
                self.subobject.add_shorten(fro,to)
            if( self.template_parameters is not None and len(self.template_parameters ) > 0 ):
                for tp in self.template_parameters:
                    tp.add_shorten(fro,to)
            if( self.parameters is not None and len(self.parameters) ):
                for p in self.parameters:
                    p.add_shorten(fro,to)
            self.shortens[fro] = to

    def __str__(self):
        return self.to_string()

    def to_string( self, indent = 0 ):
        if( self.cached_string is not None ):
            return self.cached_string

        selfname = self.name
        if( selfname is None ):
            selfname = ""
        elif( len(selfname) == 0 ):
            return ""

        ret = ""
        if( self.namespace is not None ):
            ret += str(self.namespace) + "::"
        ret += selfname
        ret = self.shorten(ret)

#        print(f"STR {self.id}:{self.name} => {self.suppress_templates}")
        if( self.template_parameters is not None ):
            if( self.suppress_templates ):
                ret += vdb.color.color("<" + fold_ellipsis.value +  ">",color_shorten.value)
            else:
                ret += "<"
                first=True
                for t in self.template_parameters:
                    if( first ):
                        first = False
                    else:
                        ret += ", "
                    ts = str(t)
#                    if( len(ts) == 0 ):
#                        print(f"Template parameter of length 0 {self.name}")
                    ret += ts
#                print("ret = '%s'" % (ret,) )
                if( ret[-1] == ">" ):
                    ret += " "
                ret += ">"
#        print("self.id = '%s'" % (self.id,) )
#        print("self.parameters = '%s'" % (self.parameters,) )
#        print("self.subobject = '%s'" % (self.subobject,) )
        if( self.parameters is not None ):
            ret += "("
            first = True

            for par in self.parameters:
                if( first ):
                    first = False
                else:
                    ret += ", "
                ret += str(par)
            ret += ")"

        if( self.subobject is not None ):
#            print("self.subobject = '%s'" % (self.subobject,) )
#            print("self.subobject.parameters = '%s'" % (self.subobject.parameters,) )
            if( self.subobject.name is not None and len(self.subobject.name) != 0 ):
                ret += self.subobject.tail
#            print("ret = '%s'" % (ret,) )
#            print("self.subobject = '%s'" % (self.subobject,) )
#            print("ret = '%s'" % (ret,) )
            ret += str(self.subobject)

        if( cache.value is True ):
            self.cached_string = ret
#        print("ret = '%s'" % (ret,) )
        return ret

def use_sofar( sofar ):
#    print("sofar = '%s'" % (sofar,) )
#    while( sofar.endswith(" ") ):
#        sofar = sofar[:-1]
    return sofar

def parse_fragment( frag, obj, level = 0 ):
#    indent(level,"Parsing fragment '{}'",frag[:250])
    sofar = ""

    ans = "(anonymous namespace)"
    une = "<unnamed enum>"

    tmpl_tailset = [ ":", "*", "&", " const" ]
    func_tailset = [ "()" ]

    swallow_next = False

    i = -1
    while i < len(frag)-1:
        i += 1

        s=frag[i]
#        print(f"@{level} frag[{i}]='{frag[i]}'")
        if( s == " "):
            if( not swallow_next ):
                if( len(sofar) > 0 ):
#                    print(f"'{sofar}' => '{sofar+s}'")
                    sofar += s
#                sofar += str(level)
            swallo_next = False
            continue
        if( s == "(" ):
            if( frag[i:].startswith(ans) ):
                # sofar should be empty. assert maybe?
                sofar = ans
                i += len(ans)
                i -= 1
                continue
            obj.add_param(None) # tell there are some, maybe 0
#            print(f"sofar use params '{sofar}'")
            obj.name = use_sofar(sofar)
            sofar = ""
            obj.set_type("function")
            while True:
                ct = type_or_function()
                ct.set_type("function parameter")
                i += 1
                consumed = parse_fragment( frag[i:], ct, level+1 ) 
                i += consumed
#                print("ct.subobject = '%s'" % (ct.subobject,) )
#				indent(level,"param {}",ct)
#                print("frag[i:] = '%s'" % frag[i:] )
#				print("frag[i+1:] = '%s'" % frag[i+1:] )
                if( len( ct.name) > 0 ):
                    obj.add_param(ct)
#                print("obj.id = '%s'" % (obj.id,) )
                if( frag[i] == ")" ):
                    if( ct.subobject is not None ):
                        obj.subobject = ct.subobject
                        obj.subobject.name = None
                        ct.subobject = None
                    break
#            print("obj.name = '%s'" % (obj.name,) )
#            print("frag[i:] = '%s'" % (frag[i:],) )
            continue
        if( s == ")" ):
#            vdb.util.bark() # print("BARK")
            obj.name = use_sofar(sofar)
#            print("obj.name = '%s'" % (obj.name,) )
            if( i+1 < len(frag) and frag[i+1] == "(" ):
                # a member funciton pointer special case thingie? parse parameters again I guess?
                sub = type_or_function()
                obj.subobject = sub
#                        print("frag[i+1:] = '%s'" % (frag[i+1:],) )
                consumed=parse_fragment( frag[i+1:],sub,level+1)
#                        print("consumed = '%s'" % (consumed,) )
                i += consumed
#                    i+=1
            return i
        if( s == ":" ):
            obj.add_ns( use_sofar(sofar) )
            sofar = ""
            continue
        if( s == "," ):
#            print("comma use_sofar '{sofar}'")
            obj.name = use_sofar(sofar)
            return i
        if( s == "<" ):
            if( frag[i:].startswith(une) ):
                sofar = une
                i += len(une)
                i -= 1
                continue
            obj.name = use_sofar(sofar)
#            print(f"sofar before '{sofar}'")
#            print(f"obj.name before '{obj.name}'")
            while True:
                ct = type_or_function()
                i+=1 # consume the <
                i += parse_fragment( frag[i:], ct,level+1 )
                ct.set_type("template parameter")
#                print(f"after template fragment @{level} frag[{i}]='{frag[i]}'")
#                print("ct.name = '%s'" % (ct.name,) )

                obj.add_template( ct )
#                print("i = '%s'" % i )
#                print("len(frag) = '%s'" % len(frag) )
#                print("frag = '%s'" % (frag,) )
#                if( i+1 == len(frag) ): # assume last closing >
#                    return i

                if( frag[i] == "," ):
                    continue
                if( frag[i] == ">" ):
#                    obj.name = use_sofar(sofar)
#                    sofar = ""
#                    print(f"tail: '{frag[i+1:]}'")
                    if( (i+1) < len(frag) ):
                        for tail in tmpl_tailset:
                            if( frag[i+1:].startswith(tail) ):
                                sub = type_or_function()
                                if( frag[i+1] == ":" ):
                                    sub.tail = "::"
                                if( frag[i+1] == " " ):
                                    sub.tail = " "
#                                print("replacing subobject")
                                obj.subobject = sub
                                obj = sub
#                                obj.name = use_sofar(sofar)
                                sofar = ""
                                break
#                    else:
#                        i+=1
#                    print("level = '%s'" % (level,) )
#                    print("i = '%s'" % (i,) )
#                    print("ct.name = '%s'" % (ct.name,) )
#                    if( level == 0 ):
#                        i += 1
                    break
            swallow_next = True
#            print(f"sofar after '{sofar}'")
#            print(f"obj.name after '{obj.name}'")
#            print("sofar = '%s'" % (sofar,) )
#            print(f"template continue on {frag} [{i}]")
            continue
        if( s == ">" ):
#            vdb.util.bark() # print("BARK")
#            print("obj.name = '%s'" % (obj.name,) )
#            print("sofar = '%s'" % (sofar,) )
            if( obj.name is not None and len(sofar) > 0 and obj.name != sofar ):
#                print("Going subobject")
                obj.subobject = type_or_function()
                obj.subobject.name = use_sofar(sofar)
                pass
            elif( obj.name is not None and len(sofar) == 0 ):
#                print("Leaving name")
                pass # leave the old name
            else:
#                print("Old overwrite")
#            if( len(sofar) > 0 ):
                obj.name = use_sofar(sofar)
            if( len(obj.name) == 0 ):
                obj.name = None
            return i

        sofar += s

#    print("len(frag) = '%s'" % (len(frag),) )
#    print("at end i = '%s'" % (i,) )
    i += 1
    if( len(sofar) > 0 ):
        obj.name = use_sofar(sofar)
#        print(f"exit obj.name '{obj.name}'")
    return i




def parse_function( fun ):
#    print("fun = '%s'" % fun )
    func = type_or_function()
    rest = fun
    sub = func
    func.set_type("type_or_function")
    i = parse_fragment( rest , sub )



    if( i != len(rest) ):
        print(f"Consumed {i} out of {len(rest)} bytes, parser doesn't know about the rest")

    sf = str(func)
    s0 = sf.replace(" ","")
    s1 = fun.replace(" ","")
    s0 = sf
    s1 = fun
    if( debug.value and s0 != s1 ):
        #		func.dump()
        print("Recreating the function signature leads a difference (%s,%s)" % (len(s0),len(s1)))
        print("fun = '%s'" % fun )
        print("sf  = '%s'" % sf )
        cnt = 0
        d0 = ""
        d1 = ""
        for i in range(0,len(s0)):
            if( s0[i] != s1[i] ):
                d0 = s0[i-10:i+40]
                d1 = s1[i-10:i+40]
                break
        print("Found   :" + d0)
        print("Expected:" + d1)
        func.dump()
    elif( s0 != s1 ):
        vdb.log( f"Failed to properly parse {fun}, shortening not possible, recommend writing  a testcase", level = 2)

#	func.dump()
    return func







def template_fold(fname,template):
	start = fname.find(template)
	if( start == -1 ):
		return fname
	start += len(template)
	prefix=fname[0:start]
#	print("start = '%s'" % start )
#	print("fname = '%s'" % fname )
#	print("prefix = '%s'" % prefix )
	level = 0
	for i in range(start,len(fname)):
		if( fname[i] == "<" ):
			level+=1
		elif( fname[i] == ">" ):
			level-=1
		if( level == 0 ):
			suffix=fname[i+1:]
			suffix=template_fold(suffix,template)
			fname = prefix + vdb.color.color("<…>",color_shorten.value) + suffix
			break
	return fname




shortens = {
        "std::basic_string<char, std::char_traits<char>, std::allocator<char> >": "std::string",
        "std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >": "std::string",
        "std::__cxx11::basic_string<wchar_t, std::char_traits<wchar_t>, std::allocator<wchar_t> >": "std::wstring",
        "std::basic_ostream<char, std::char_traits<char> >": "std::ostream",
        "std::basic_ostream<wchar_t, std::char_traits<wchar_t> >": "std::wostream",
        "(anonymous namespace)": "(anon)",
        "<unnamed enum>": "<enum>",
        }

def add_shorten( f, t ):
    shortens[f] = t

def add_shorten_v( argv ):
    if( len(argv) != 2 ):
        print("add_shorten expects exactly 2 arguments, %s given" % len(argv) )
    else:
        f=argv[0]
        t=argv[1]
        add_shorten(f,t)
        print(f'Added shorten from "{f}" to "{t}"')
    global symbol_cache
    symbol_cache = {}

def show_shorten( args ):
    print("Configured shortens are:")
    mlen=0
    for s,t in shortens.items():
        mlen = max(mlen,len(s))

    mlen += 2
    for s,t in shortens.items():
        xs="'"+s+"'"
        print(f"{xs:<{mlen}} => '{t}'")


vdb.subcommands.add_subcommand( [ "add", "shorten" ], add_shorten_v )
vdb.subcommands.add_subcommand( [ "show", "shorten" ], show_shorten )




foldables = [ ]
conditional_foldables = {
        ".*" : []
        }

def add_foldable( fld ):
    if( isinstance(fld,str) ):
        add_foldable(fld.splitlines())
    else:
        for f in fld:
            f = f.strip()
            if( len(f) > 0 ):
                foldables.append(f)

def add_conditional( cond, fld = None ):
    foldables = conditional_foldables.get(cond,[])
    if( isinstance(fld,str) ):
        foldables.append(fld)
    else:
        for f in fld:
            foldables.append(d )

def add_foldable_v( argv ):
    if( len(argv) not in [1,2] ):
        print("add_foldable expects 1 or 2 arguments, %s given" % len(argv) )
    elif( len(argv) == 1 ):
        add_foldable( argv[0] )
    elif( len(argv) == 2 ):
        add_conditional( argv[0], argv[1] )
    global symbol_cache
    symbol_cache = {}

def show_foldable( args ):
    print("Foldables are:")
    for f in foldables:
        print(f"'{f}'")

    print("Conditional foldables:")
    for c,fl in conditional_foldables.items():
        print(c)
        for f in fl:
            print(f"    {f}")

vdb.subcommands.add_subcommand( [ "add","foldable" ], add_foldable_v )
vdb.subcommands.add_subcommand( [ "show","foldable"] , show_foldable )

lazy_task = None

loaded_typedefs = False
def lazy_load_typedefs( x = None):
    global loaded_typedefs
    if( loaded_typedefs ):
        return

    global lazy_task
    # Already one task running?
    if( lazy_task is not None ):
        return

    lazy_task = vdb.util.async_task( async_load_typedefs )
    lazy_task.start()

def async_load_typedefs( at ):
    try:
        print("Loading typelist in background...")
        print("WARNING! Due to gdb/python instabilities doing anything during that phase may lead to crashes")
        at.set_progress("[ types #/# ]")
        load_typedefs( at )
        print("Finished loading typelist")
    except:
        traceback.print_exc()
    finally:
        at.set_progress(None)
    global lazy_task
    lazy_task = None

def load_typedefs( at ):
    loaded_typedefs = True
    typelist = gdb.execute("info types",False,True)

    candidates = set()
    cnt = 0

    typelines = typelist.splitlines()
    for line in typelines:
        cnt += 1
        # Want only typedefs of templates
        if( line.find("typedef") != -1 and line.find("<") != -1 ):
            sl = line.split()
            # Rules out typdef members of templates
            if( sl[-1].find(">") != -1 ):
                continue
            if( sl[0] == "File" ):
                continue
            if( sl[1] != "typedef" ):
                print("Not sure what this is: %s" % line)
                continue
            sl = sl[2:]
            to = sl[-1]
            if( to.endswith(";") ):
                to = to[:-1]
            fr = " ".join(sl[:-1])
            if( len(fr) > len(to) ):
                candidates.add((fr,to))
        if( cnt % 100 == 0 ):
            at.set_progress( f"[ types {cnt}/{len(typelines)}({len(candidates)}) ]" )


    for fr,to in candidates:
        if( verbosity.value > 2 ):
            print("Shortening '%s' => '%s'" % (fr,to))
        add_shorten(fr,to)
        # XXX Try to fully replicate the gdb provided type string, including all spaces
#        pfr = parse_function(fr)
#        add_shorten(str(pfr),to)
    print(f"Loaded {cnt} type items, found {len(candidates)} for automatic typedef shortening")


symbol_cache = {}

lazy_hint = True

def symbol(fname):

    global lazy_hint

    if( lazy.value ):
        lazy_load_typedefs()
    elif( lazy_hint ):
        lazy_hint = False
        vdb.util.log("Lazy typedef loading is disabled. To manually load typedefs for shortening, do vdb load shorten")

    if( fname is None ):
        return fname

    infname = fname

    global symbol_cache
    if( cache.value ):
        fret = symbol_cache.get(fname,None)
        if( fret is not None ):
            return fret
    else:
        symbol_cache = {}

    fun = parse_function(fname)
    if( debug.value ):
        g = vdb.dot.graph("function")
        fun.to_dot(g)
        g.write("shorten.dot")

#    print("debug.value = '%s'" % (debug.value,) )
#    print( f"'{fun}' =?= '{fname}'")
#    print("bef = '%s'" % (fun,) )
    fun.add_shorten(shortens)
    fun.fold_templates(foldables)

    # Do the shortens on the complete string type too
    fname=str(fun)

    cnt = 0
    while True:
        cnt += 1
        ofname = fname
        for old,new in shortens.items():
            fname = fname.replace(old,new)
        if( ofname == fname ):
            break
        if( cnt >= recursion_limit.value ):
            break

    symbol_cache[infname] = fname
#    print(f"[{len(symbol_cache)}]{infname} => {fname}")
    return fname

def symbol_cmd(args):
    sym = " ".join(args)
    print(symbol(sym))

vdb.subcommands.add_subcommand( [ "shorten"] , symbol_cmd )
vdb.subcommands.add_subcommand( [ "load", "shorten"] , lazy_load_typedefs )

# This does some of the work of load_typedefs, maybe share it
def test_all(args):
    typelist = gdb.execute("info types",False,True)

    prere = re.compile("^[0-9]*:(.*)")
    cnt = 0
    for line in typelist.splitlines():
        line=line.strip()
        if( len(line) == 0 ):
            continue
        if( line[-1] == ":" ):
            continue

        m = prere.match(line)
        if( m is not None ):
            line = m.group(1).strip()
        if( line[-1] == ";" ):
            line = line[:-1]
        # They should be in the list as types elsewhere anyways
        if( line.startswith("typedef") ):
            continue

        fun = parse_function(line)
        cnt += 1
    print(f"Tested {cnt} type strings...")

vdb.subcommands.add_subcommand( [ "_test_all_shorten"] , test_all )

# vim: tabstop=4 shiftwidth=4 expandtab ft=python
