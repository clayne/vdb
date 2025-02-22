#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import vdb
import vdb.shorten
import vdb.util
import vdb.cache

import gdb

import itertools
import re
import traceback
import sys


mod=sys.modules[__name__]
vdb.enabled_modules["layout"] = mod

class D_byte_descriptor:
    """
    Majority of cases we care about what the single bytes belong to, and that is what this object is about
"""
    def __init__(self,prefix,fname,ftype):
        self.prefix = prefix
        self.type = ftype
        self.code = None
        self.object = None
        # They be None if the full byte is used by this object
        self.bit_start = None
        self.bit_end = None

    def __str__( self ):
        return f"<byte_descriptor prefix={self.prefix} type={self.type} code={vdb.util.gdb_type_code(self.code)} object={self.object} bit_start={self.bit_start} bit_end={self.bit_end}>"

    def name( self ):
        vdb.util.bark() # print("BARK")
        print(f"name({str(self)})")
#        print("self.prefix = '%s'" % self.prefix )
#        print("self.object.name = '%s'" % self.object.name )
        if( self.prefix is not None and len(self.prefix) > 0 ):
            return f"{self.prefix}{self.object.name}"
        else:
            if( self.object is not None ):
#        return f"{self.prefix}::{self.member_name}"
                return f"{self.object.name}"
"""


Plan:

    an "outer" object descriptor that is collecting info for each byte. In case we need bitfields we use a special version of a byte descriptor.

    The byte descriptor has a reference to the actual type object it refers to. This can be traversed through their
    parent references unto the root object, allowing to reconstruct a complete name. For reasons of convenience and
    speed we shall provide a "flattened down" version of the access descriptor (if we have the information we might
    colour it according to private/protected/public)

    This should make it possible to trivially implement pahole by going through all the bytes. We should take care of
    the bytes all having the same descriptor object so we can easily do the condensed implementation by using "is"
    object identity.

    Similar we operate for the hexdump annotation by checking for each byte if we have to change the description and colour

    For the ftree view things might get a bit more complicated. We should recurse down and the subobjects return the
    rowcount they occupy, then we set rowspan for the td. We do that by traversing recursively bottom up through the
    parsed type tree. This process will then also lead to a list of pointers to other objects. First we extract those of subobjects of ourself.

    For accessing unions we need to have some way to help chosing. Per default we should maybe take either the most
    complicated type or a non pointer type.

    Additionally for subobjects we need to have a mechanism to cast them to other types that they might not be unioned
    of, for things like std containers that store their objects in aligned buffers. 

    For the actual values we have two options: trying to get them via op[] on the values, and trying to cast offsets to
    that object. We should use the later only when we can't get it via the earlier, or for downcasting/crosscasting.






"""

class object:

    def __init__( self, gtype, field = None ):
        self.type = gtype
        self.size = gtype.sizeof
#        self.offset = -1
        self.subobjects = []
        self.field = field
        if( field is not None ):
#            print("field = '%s'" % (field,) )
#            print("field.name = '%s'" % (field.name,) )
#            print("field.bitpos = '%s'" % (field.bitpos,) )
#            print("field.bitsize = '%s'" % (field.bitsize,) )
            self.name = field.name
            self.bit_offset = int(field.bitpos)
            self.byte_offset = self.bit_offset // 8
            self.is_base_class = field.is_base_class
            if( field.bitsize > 0 ):
                self.bit_size = field.bitsize
            else:
                self.bit_size = None
        else:
            self.name = "<anonymous>"
            self.bit_offset = -1
            self.byte_offset = -1
            self.is_base_class = False
            self.bit_size = None
        self.final = False
        self.parent = None
        self.union = False
        # Don't clone that one
        self.index = None
#        print("self.byte_offset = '%s'" % (self.byte_offset,) )

    def clone( self ):
        ret = object( self.type, None )
        ret.type          = self.type
        ret.size          = self.size
#        ret.offset        = self.offset
        ret.field         = self.field
        ret.name          = self.name
        ret.bit_offset    = self.bit_offset
        ret.byte_offset   = self.byte_offset
        ret.is_base_class = self.is_base_class
        ret.final         = self.final
        ret.parent        = self.parent

        for so in self.subobjects:
            cl = so.clone()
            cl.parent = ret
            ret.subobjects.append( cl )
        return ret

    def get_base( self ):
        if( self.parent ):
            return self.parent.get_base()
        else:
            return self

    def get_path( self, recursive = False ):
        path = ""
        if( self.parent is not None ):
            if( self.name is None ):
                xname = "<anonymous>"
            else:
                xname = self.name

            path = self.parent.get_path(True) + "::{" + str(self.type.strip_typedefs()) + "}::" + xname
        return path

    def __str__(self):
        s = f"{self.type}[{self.size}] : {self.name}, @{len(self.subobjects)},b{self.is_base_class} [{self.index}]{{{self.byte_offset}}} {self.byte_offset=}, {self.bit_offset=} {self.bit_size=} {self.final=}"
        return s

    def __repr__(self):
        return str(self)

cgdb = vdb.cache.execute_cache()

def get_vtt_name( atype, name = None ):
    VTT=None
    try:
        if name is None:
            name = atype.name
        cmd = "p &'VTT for %s'" % (name)
#        print("cmd = '%s'" % cmd )
#        ofresult = gdb.execute(cmd,False,True)
        global cgdb
        ofresult = cgdb.execute(cmd,False,True)
#        print("ofresult = '%s'" % ofresult )
        g = re.search("(0x[a-fA-F0-9]*) <VTT for",ofresult)
#        print("g = '%s'" % g )
        VTT=g.group(1)
#        print("VTT = '%s'" % VTT )
    except:
#        vdb.print_exc()
        pass
    return VTT

def get_vtable_entry( name, offset ):
    try:
        cmd="((ptrdiff_t*)&'vtable for %s')[%s]" % (name,offset)
#    print("cmd = '%s'" % (cmd,) )
        ofresult = gdb.parse_and_eval(cmd)
    except gdb.error:
        cmd="((unsigned long*)&'vtable for %s')[%s]" % (name,offset)
#    print("cmd = '%s'" % (cmd,) )
        ofresult = gdb.parse_and_eval(cmd)
#    print("ofresult = '%s'" % (ofresult,) )
    return ofresult

def get_vtt_entry( vtt, offset ):
#    print("vtt = '%s'" % vtt )
#    print("offset = '%s'" % offset )
#    cmd="p ((uint64_t*)(%s%s))[0]" % (vtt,offset)
    cmd="p ((uint32_t*)(%s%s))[0]" % (vtt,offset)

#    print("cmd = '%s'" % cmd )
#    ofresult = gdb.execute(cmd,False,True)
    global cgdb
    ofresult = cgdb.execute(cmd,False,True)
#    print("ofresult = '%s'" % ofresult )
    g = re.search("= ([0-9]*)",ofresult)
    newoffset=g.group(1)
#    print("g = '%s'" % g )
    offset = int(newoffset)
    return offset

object_cache = { }

class object_layout:
    # Can be called with just a type, or just a value. If both are passed, the values type overrides the passed type.
    # The value is only ever useful for when we deal with virtual pointers and could infer from it a different type
    def __init__( self, otype = None, value = None ):
#        print(f"object_layout({otype=},{value=})")

        if( otype is None ):
            otype = value.type

        if( value is not None and value.type is not None and otype is not None ):
            if( value.type != otype ):
                print(f"Warning: {otype=} and {value.type=} are different in object_layout")
        # The object is optimized in a way to be held in a register or not exist at runtime at all
        if( value is not None and value.address is None ):
            value = None

        self.type = otype
        self.value = value
        self.vtt = get_vtt_name(self.type)
#        print("self.vtt = '%s'" % (self.vtt,) )
#        print("type(self.vtt) = '%s'" % type(self.vtt) )

        # Unless its a vtt name we strip typedefs ( with vtt we have possibly multiple inheritance and need to  do
        # tricks to get sizes and offsets correct )
        if( self.vtt is None ):
            xtype = self.type.strip_typedefs()
#            print("xtype = '%s'" % xtype )
#            print("(xtype == self.type) = '%s'" % (xtype == self.type) )
            if( xtype.name != self.type.name ):
                self.type = xtype
                self.vtt = get_vtt_name(self.type)
        self.vtype = None
#        print("self.type = '%s'" % self.type )
#        print("self.vtt = '%s'" % self.vtt )


#        print("self.value = '%s'" % self.value )
#        print("self.type = '%s'" % self.type )
#        print("self.value.dynamic_type = '%s'" % self.value.dynamic_type )
        if( self.value is not None ):
#            print(f"{value}")
#            print(f"{self.value.address=}")
            self.vtype = vdb.util.guess_vptr_type( self.value.address ).type.target()
#            print("self.type = '%s'" % (self.type,) )
#            print("self.type.sizeof = '%s'" % (self.type.sizeof,) )
#            print("self.vtype = '%s'" % (self.vtype,) )
#            print("self.vtype.sizeof = '%s'" % (self.vtype.sizeof,) )
            # Here it can happen that when the derived classes have no virtual functions, the vptr is "stuck" at a base
            # class type ( there really is no point in duplicating a virtual table that isn't different at all).
            # Unfortunately for us, this is the same when an object is already destroyed
            # XXX It would be nice if we could detect the difference and issue a warning for already destroyed objects

            # Lets chose the one with the biggest size, after all we are interested in the layout of the members and
            # don't care if the virtual table is really correct here.
            if( self.value.dynamic_type is not None and self.value.dynamic_type.sizeof > self.type.sizeof ):
                self.type = self.value.dynamic_type
            if( self.vtype is not None and self.vtype.sizeof > self.type.sizeof ):
                self.type = self.vtype
#        print("self.type.sizeof = '%s'" % (self.type.sizeof,) )
        self.type = self.type.strip_typedefs()
#        print("self.type.sizeof = '%s'" % (self.type.sizeof,) )
#        self.bytes = list(itertools.repeat(byte_descriptor(None,None,None),self.type.sizeof))
#        self.descriptors = []
#        print("self.vtype = '%s'" % self.vtype )

#        print("self.type == type = '%s'" % (self.type == type ))
#        print("self.type is type = '%s'" % (self.type is type ))

#        print("self.type == self.value.dynamic_type = '%s'" % (self.type == self.value.dynamic_type ))
#        print("self.type == self.value.type = '%s'" % (self.type == self.value.type ))
#        print("self.type == self.vtype = '%s'" % (self.type == self.vtype ))
#        print("self.value.dynamic_type == self.vtype = '%s'" % (self.value.dynamic_type == self.vtype ))

#        print("self.type is self.value.dynamic_type = '%s'" % (self.type is self.value.dynamic_type ))
#        print("self.type is self.value.type = '%s'" % (self.type is self.value.type ))
#        print("self.type is self.vtype = '%s'" % (self.type is self.vtype ))
#        print("self.value.dynamic_type is self.vtype = '%s'" % (self.value.dynamic_type is self.vtype ))

        global object_cache
        self.object = object_cache.get(str(self.type),None)
        if( self.object is not None ):
            self.object = self.object.clone()
            return

        self.object = object(self.type)
#        self.object.offset = 0
        self.object.name = str(self.type)
        # chose how to print the scope of the outer thing 
        if( self.value is not None ):
            self.object.is_base_class = False
        else:
            self.object.is_base_class = True
#        print("self.type = '%s'" % self.type )
#        print("self.object = '%s'" % self.object )
        self.parse(self.type,self.object)
#        for i in range(0,len(self.bytes)):
#            b = self.bytes[i]
#            o = self.bytes[i].object
##            print("%s " % i, end="")
#            if( o is None or not o.final ):
#                b.prefix = None
##                print("<unused>")
##                print("o = '%s'" % o )
#            else:
#                xo = o.parent
#                fullname = ""
#                while( xo is not None ):
#                    if( xo.is_base_class ):
#                        fullname = xo.name + "::" + fullname
#                    else:
#                        if( xo.name is None ):
#                            fullname = "{union}" + "." + fullname
#                        else:
#                            fullname = xo.name + "." + fullname
#                    xo = xo.parent
#                b.prefix = fullname
##                print(f"{o.type.strip_typedefs()} {fullname}")

    def extract_fields( self, atype ):
        ret = []
        uninteresting_codes = set()

#        print("atype = '%s'" % atype )

        try:
            # This throws when the object has no subobjects, thus it is a plain type
            for f in atype.fields():
                if( f.type.code in uninteresting_codes ):
                    continue
                else:
                    ret.append(f)
        except:
            self.object = object(self.type)
#            print("self.object.name = '%s'" % (self.object.name,) )
            self.object.byte_offset = 0
#            bd = byte_descriptor(None,None,None)
#            bd.object = self.object
#            bd.object.final = True
#            bd.prefix = ""
#            self.descriptors.append(bd)
            self.final = True
#            print("self = '%s'" % self )
            pass

        return ret

    def flatten( self, obj = None, prefix = ""):
        ret = self._flatten(obj,prefix)
        return ( ret, self.type.sizeof * 8 )

    def _flatten( self, obj , prefix ):
        if( obj is None ):
            obj = self.object
            prefix = obj.name + "::"
        ret = []
        for o in obj.subobjects:
            if( not o.final ):
                name = o.name
                if( name is None ):
                    if( o.union ):
                        name = "<union>"
                    else:
                        name = "???"
                ret += self._flatten( o,prefix + name + "::" )
                continue
            # If it is final and a base class its an empty base, leave it out
            if( o.is_base_class ):
                continue

            ret.append( ( o.bit_offset, prefix  + o.name, o ) )
        return ret

    def parse( self, atype, parent, offset = 0 ):
#        print("atype = '%s'" % atype )
        
        basecnt = 0
        baseidx = 1
        ef = self.extract_fields(atype)
        for f in ef:
            if( hasattr(f,"bitpos") and f.bitpos is None ):
                basecnt += 1
#        print("basecnt = '%s'" % (basecnt,) )
        for f in ef:
            if( not hasattr(f,"bitpos") ):
                # Ignore static fields
                continue
            # Skip fields where gdb doesn't know it (at the moment virtual base classes due to some bug)
            if( f.bitpos is None ):
                xname = atype.name + "::" + f.type.name
                # 0x28 0x20 0x14
#                print("xname = '%s'" % (xname,) )
                vtt = get_vtt_name( atype )
#                print("atype.name = '%s'" % (atype.name,) )
#                print("f.type.name = '%s'" % (f.type.name,) )
#                print("vtt = '%s'" % (vtt,) )
                vof = get_vtable_entry( atype.name, basecnt - baseidx )
#                print("vof = '%s'" % (vof,) )
                baseidx += 1
#                f.bitpos = int( -8 * vof )
                f.bitpos = int( 8 * vof )
#                print("f.bitpos = '%s'" % (f.bitpos,) )
#                continue
#            print("")
            so = object(f.type,f)
#            so.offset = offset
#            print("parent = '%s'" % parent )
            so.parent = parent
            parent.subobjects.append(so)
#            print(" . . . . . . . . . . . . . ")
#            print("f.is_base_class = '%s'" % (f.is_base_class,) )
#            print("so.name = '%s'" % so.name )
#            print(f"{so.type=}")

#            print("so.type.strip_typedefs() = '%s'" % so.type.strip_typedefs() )
#            print("so.bit_offset = '%s'" % so.bit_offset )
#            print("so.byte_offset = '%s'" % so.byte_offset )
#            print("so.size = '%s'" % so.size )
#            print("offset = '%s'" % offset )
#            print("")
#            bd = byte_descriptor(None,None,None)
#            bd.object = so
#            print(f"{so=}")
            if( so.bit_offset >= 0 ):
                so.byte_offset += offset
#                print("so = '%s'" % so )
#                print("offset = '%s'" % offset )
                if( not f.is_base_class ):
                    pass
#                    print("len(self.bytes) = '%s'" % (len(self.bytes),) )
#                    print("so.byte_offset = '%s'" % (so.byte_offset,) )
#                    print("so.size = '%s'" % (so.size,) )
#                    for i in range( so.byte_offset, so.byte_offset + so.size ):
#                        if( i >= len(self.bytes) ): # Temporary workaround for not being able to support bitfields
#                            break
#                        print("self.bytes[%s] = '%s' => '%s'" % (i,self.bytes[i].name(),bd.name()) )
#                        self.bytes[i] = bd
#                self.descriptors.append(bd)
            else:
#                print("so = '%s'" % so )
                voffset = get_vtt_entry( self.vtt, so.byte_offset )
                so.byte_offset = voffset
#                print("so.byte_offset = '%s'" % (so.byte_offset,) )
#                print("VIRTUAL")
                # Virtual, get the real position
                pass
            so.bit_offset += offset * 8
            code = so.type.strip_typedefs().code
#            if( f.is_base_class ):
#                self.parse( so.type, so, offset )
#            print("vdb.util.gdb_type_code(code) = '%s'" % vdb.util.gdb_type_code(code) )
            if( code == gdb.TYPE_CODE_STRUCT ):
                # empty subobjects can sometimes occupy space too
                if( len(f.type.fields()) == 0 ):
                    so.final = True
#                    print("STRUCT LAYOUT so = '%s'" % so )
                else:
#                    print("so = '%s'" % so )
                    self.parse( so.type, so, so.byte_offset )
            elif( code == gdb.TYPE_CODE_UNION ):
                self.parse( so.type, so, so.byte_offset )
                so.union = True
#                print("Sorry, unions not yet properly supported")
            else:
#                print("so.type.code = '%s'" % so.type.code )
                so.final = True
#                print("LAYOUT ELSE so = '%s'" % so )
#            print("so = '%s'" % so )

#TODO is it maybe a good idea to map all integer types to standard integer [u]int32_t etc. ones?

# vim: tabstop=4 shiftwidth=4 expandtab ft=python
