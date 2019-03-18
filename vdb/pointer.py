#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import vdb.config
import vdb.color
import vdb.vmmap
import vdb.util
import vdb.asm

import gdb

import string
from enum import Enum,auto


arrow_right = vdb.config.parameter("vdb-pointer-arrow-right", " → " )
arrow_left = vdb.config.parameter("vdb-pointer-arrow-left", " ←  " )
arrow_infinity = vdb.config.parameter("vdb-pointer-arrow-left", " ↔ " )

ellipsis = vdb.config.parameter("vdb-pointer-ellipsis", "…" )


gdb_void     = None
gdb_void_ptr = None
gdb_void_ptr_ptr = None

def update_types( ):
    global gdb_void
    global gdb_void_ptr
    global gdb_void_ptr_ptr
    gdb_void     = gdb.lookup_type("void")
    gdb_void_ptr = gdb_void.pointer()
    gdb_void_ptr_ptr = gdb_void_ptr.pointer()

def read( ptr ):
    result = None
    addr=ptr
    count=1
    try:
        result = gdb.selected_inferior().read_memory(addr, count)
    except gdb.error:
        pass
    return result

def as_c_str( ptr, maxlen = 64 ):
    c_str = bytearray()
    rptr = ptr

    for i in range(0,maxlen):
        b = read(rptr)
        if( b is None ):
            break
        b = b[0]
#        print("type(b) = '%s'" % type(b) )
        rptr += 1
        if( b == b'\x00' ):
            break
        ib = int.from_bytes(b,byteorder="little")
        if( ib == (0x7f & ib) ):
            if( b.decode("ascii") not in string.printable ):
                break
        c_str += b
    if( len(c_str) > 0 ):
        c_str = vdb.util.maybe_utf8(c_str)
        return c_str
    else:
        return None

def annotate( ptr ):
    mv=gdb.parse_and_eval("(void*)(%s)" % int(ptr) )
    mv = str(mv)
    pbs = mv.find("<")
    if( pbs != -1 ):
        mv = mv[pbs:]
        return mv
    return None

def as_tail( ptr ):
    s = as_c_str(ptr)
    if( s is not None ):
        return f"[{len(s)}]'{s}'"

    at = vdb.memory.mmap.get_atype( ptr )
#    print("at = '%s'" % at )
    if( at  == vdb.memory.access_type.ACCESS_EX ):
        return vdb.asm.get_single(ptr)
#    if( mt == vdb.memory.memory_type.CODE ):
    return None

def color( ptr, archsize ):
    """Colorize the pointer according to the currently known memory situation"""
    ptr=vdb.util.xint(ptr)
    plen = archsize // 4
#    t,additional = get_type(ptr,archsize)

    s,mm,col,additional = vdb.memory.mmap.color(ptr,colorspec="Asma")
#    scolor = colormap.get(t,color_unknown)

    if( mm.mtype == vdb.memory.memory_type.NULL ):
        ps = f"{ptr:0{plen}x}"
        ps0 = ""
        ps1 = ""
        rest=False
        for p in ps:
            if( rest or p != "0" ):
                ps1 += p
                rest = True
            else:
                ps0 += p
        ret = vdb.color.color("0x" + ps0,col) + ps1
    else:
#        print("ptr %x of type %s" % (ptr,t))
        ret = vdb.color.color(f"0x{ptr:0{plen}x}",col)
#    return ( ret, additional )
    return ( ret, additional, col, mm )

def chain( ptr, archsize, maxlen = 8 ):
    if( gdb_void == None ):
        update_types()
    if( maxlen == 0 ):
        return ellipsis.value

#    print("chain(0x%x,…)" % ptr )
#    print("type(ptr) = '%s'" % type(ptr) )
    ret,add = color(ptr,archsize)

    an = annotate( ptr )
    plen = archsize // 4
    plen += 4
    if( an ):
        ret += f" {an:<{plen}}"
    s = as_tail(ptr)
    if( s is not None ):
        ret += f"{arrow_right.value}{s}"
        return ret
    if( add is not None ):
        ret += f"   {add[1]}"
    try:
        gptr = gdb.Value(ptr)
#        print("gptr = '%s'" % gptr )
#        print("gptr.type = '%s'" % gptr.type )
#        xptr = gptr.cast(gdb_void_ptr)
        xptr = gptr.cast(gdb.lookup_type("void").pointer())
#        print("xptr = '%s'" % xptr )
#        print("xptr.type = '%s'" % xptr.type )
        xptr = gptr.cast(gdb_void_ptr)
#        print("xptr = '%s'" % xptr )
#        print("xptr.type = '%s'" % xptr.type )
        nptr = gptr.cast(gdb_void_ptr_ptr)
#        print("nptr = '%s'" % nptr )
        gvalue = nptr.dereference()
        if( nptr == gvalue ):
            ret += arrow_infinity.value + color(gvalue,archsize)[0]
        else:
#        print("gvalue = '%s'" % gvalue )
            ret += arrow_right.value + chain(gvalue,archsize,maxlen-1)
    except gdb.MemoryError as e:
#        print("e = '%s'" % e )
        pass
    except:
        raise
    return ret




# vim: tabstop=4 shiftwidth=4 expandtab ft=python
