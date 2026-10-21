"""Minimal MIPS (R3000) encoder for the program patches: only the instructions used.
Mind the R3000 load delay slot: the instruction after a load must not use its result.

lines are ('op', args...) tuples or 'label:' strings; registers by name without
'$'; memory operands as ('lw', rt, offset, base). Branch/jump targets are labels or
absolute addresses.
"""
import struct

R = {n: i for i, n in enumerate(
    'zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 '
    't8 t9 k0 k1 gp sp fp ra'.split())}

def _i(op, rs, rt, imm):
    return op << 26 | R[rs] << 21 | R[rt] << 16 | imm & 0xFFFF

def _r(rs, rt, rd, sa, fn):
    return R[rs] << 21 | R[rt] << 16 | R[rd] << 11 | sa << 6 | fn

def labels(origin, lines):
    """{label: address} of an assemble() listing."""
    out, pc = {}, origin
    for ln in lines:
        if isinstance(ln, str):
            out[ln.rstrip(':')] = pc
        else:
            pc += 4
    return out

LOADS = {'lb', 'lbu', 'lh', 'lhu', 'lw'}
# operand positions read by each instruction
_READS = {'sw': (0, 2), 'sb': (0, 2), 'sh': (0, 2), 'beq': (0, 1), 'bne': (0, 1),
          'mult': (0, 1), 'multu': (0, 1), 'div': (0, 1), 'divu': (0, 1), 'jr': (0,), 'jalr': (0,),
          'beqz': (0,), 'bnez': (0,), 'bltz': (0,), 'bgez': (0,), 'blez': (0,), 'bgtz': (0,),
          'addu': (1, 2), 'subu': (1, 2), 'or': (1, 2), 'xor': (1, 2), 'nor': (1, 2), 'and': (1, 2),
          'sltu': (1, 2), 'slt': (1, 2), 'sllv': (1, 2), 'srlv': (1, 2),
          'sll': (1,), 'srl': (1,), 'sra': (1,), 'addiu': (1,), 'sltiu': (1,), 'slti': (1,),
          'andi': (1,), 'ori': (1,), 'lb': (2,), 'lbu': (2,), 'lh': (2,), 'lhu': (2,), 'lw': (2,)}


def check_delay(lines):
    """Raise on a load whose result is read by the next instruction (R3000 load delay).
    A load in a branch delay slot is not followed to the branch target."""
    ins = [ln for ln in lines if not isinstance(ln, str)]
    for a, b in zip(ins, ins[1:]):
        if a[0] in LOADS and a[1] != 'zero':
            reads = [b[1 + k] for k in _READS.get(b[0], ()) if 1 + k < len(b)]
            if a[1] in reads:
                raise ValueError('load delay: %r then %r' % (a, b))


def assemble(origin, lines):
    """lines: ('op', args...) tuples or 'label:' strings. Branch/jump targets are
    labels or absolute addresses. Returns bytes."""
    check_delay(lines)
    labels, pc = {}, origin
    for ln in lines:
        if isinstance(ln, str):
            labels[ln.rstrip(':')] = pc
        else:
            pc += 4
    out, pc = [], origin
    for ln in lines:
        if isinstance(ln, str):
            continue
        op, *a = ln
        tgt = lambda x: labels[x] if isinstance(x, str) else x
        off = lambda x: (tgt(x) - pc - 4) >> 2
        w = {
            'nop': lambda: 0,
            'addiu': lambda: _i(0x09, a[1], a[0], a[2]),
            'sltiu': lambda: _i(0x0B, a[1], a[0], a[2]),
            'andi': lambda: _i(0x0C, a[1], a[0], a[2]),
            'ori': lambda: _i(0x0D, a[1], a[0], a[2]),
            'lui': lambda: _i(0x0F, 'zero', a[0], a[1]),
            'lbu': lambda: _i(0x24, a[2], a[0], a[1]),
            'lhu': lambda: _i(0x25, a[2], a[0], a[1]),
            'beq': lambda: _i(0x04, a[0], a[1], off(a[2])),
            'bne': lambda: _i(0x05, a[0], a[1], off(a[2])),
            'beqz': lambda: _i(0x04, a[0], 'zero', off(a[1])),
            'bnez': lambda: _i(0x05, a[0], 'zero', off(a[1])),
            'b': lambda: _i(0x04, 'zero', 'zero', off(a[0])),
            'j': lambda: 0x02 << 26 | (tgt(a[0]) >> 2) & 0x3FFFFFF,
            'jr': lambda: _r(a[0], 'zero', 'zero', 0, 0x08),
            'addu': lambda: _r(a[1], a[2], a[0], 0, 0x21),
            'sll': lambda: _r('zero', a[1], a[0], a[2], 0x00),
            'subu': lambda: _r(a[1], a[2], a[0], 0, 0x23),
            'or': lambda: _r(a[1], a[2], a[0], 0, 0x25),
            'xor': lambda: _r(a[1], a[2], a[0], 0, 0x26),
            'nor': lambda: _r(a[1], a[2], a[0], 0, 0x27),
            'sltu': lambda: _r(a[1], a[2], a[0], 0, 0x2B),
            'srl': lambda: _r('zero', a[1], a[0], a[2], 0x02),
            'slt': lambda: _r(a[1], a[2], a[0], 0, 0x2A),
            'mult': lambda: _r(a[0], a[1], 'zero', 0, 0x18),
            'multu': lambda: _r(a[0], a[1], 'zero', 0, 0x19),
            'mfhi': lambda: _r('zero', 'zero', a[0], 0, 0x10),
            'slti': lambda: _i(0x0A, a[1], a[0], a[2]),
            'sra': lambda: _r('zero', a[1], a[0], a[2], 0x03),
            'sllv': lambda: _r(a[2], a[1], a[0], 0, 0x04),
            'srlv': lambda: _r(a[2], a[1], a[0], 0, 0x06),
            'and': lambda: _r(a[1], a[2], a[0], 0, 0x24),
            'jalr': lambda: _r(a[0], 'zero', 'ra', 0, 0x09),
            'bltz': lambda: _i(0x01, a[0], 'zero', off(a[1])),
            'bgez': lambda: 0x01 << 26 | R[a[0]] << 21 | 1 << 16 | off(a[1]) & 0xFFFF,
            'blez': lambda: _i(0x06, a[0], 'zero', off(a[1])),
            'bgtz': lambda: _i(0x07, a[0], 'zero', off(a[1])),
            'lb': lambda: _i(0x20, a[2], a[0], a[1]),
            'lh': lambda: _i(0x21, a[2], a[0], a[1]),
            'div': lambda: _r(a[0], a[1], 'zero', 0, 0x1A),
            'divu': lambda: _r(a[0], a[1], 'zero', 0, 0x1B),
            'mflo': lambda: _r('zero', 'zero', a[0], 0, 0x12),
            'jal': lambda: 0x03 << 26 | (tgt(a[0]) >> 2) & 0x3FFFFFF,
            'lw': lambda: _i(0x23, a[2], a[0], a[1]),
            'sw': lambda: _i(0x2B, a[2], a[0], a[1]),
            'sb': lambda: _i(0x28, a[2], a[0], a[1]),
            'sh': lambda: _i(0x29, a[2], a[0], a[1]),
        }[op]()
        out.append(struct.pack('<I', w))
        pc += 4
    return b''.join(out)

def lo(addr):
    return addr - ((addr + 0x8000) & 0xFFFF0000)

def hi(addr):
    return ((addr + 0x8000) >> 16) & 0xFFFF
