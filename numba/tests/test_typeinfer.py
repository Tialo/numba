from __future__ import print_function, division, absolute_import

import os, sys, subprocess
import itertools

import numpy as np

from numba import unittest_support as unittest
from numba.compiler import compile_isolated
from numba import types, typeinfer, typing, jit, errors
from numba.typeconv import Conversion

from .support import TestCase
from .test_typeconv import CompatibilityTestMixin


i8 = types.int8
i16 = types.int16
i32 = types.int32
i64 = types.int64
u8 = types.uint8
u16 = types.uint16
u32 = types.uint32
u64 = types.uint64
f32 = types.float32
f64 = types.float64
c64 = types.complex64
c128 = types.complex128


class TestArgRetCasting(unittest.TestCase):
    def test_arg_ret_casting(self):
        def foo(x):
            return x

        args = (i32,)
        return_type = f32
        cres = compile_isolated(foo, args, return_type)
        self.assertTrue(isinstance(cres.entry_point(123), float))
        self.assertEqual(cres.signature.args, args)
        self.assertEqual(cres.signature.return_type, return_type)

    def test_arg_ret_mismatch(self):
        def foo(x):
            return x

        args = (types.Array(i32, 1, 'C'),)
        return_type = f32
        try:
            cres = compile_isolated(foo, args, return_type)
        except errors.TypingError as e:
            pass
        else:
            self.fail("Should complain about array casting to float32")

    def test_invalid_arg_type_forcing(self):
        def foo(iters):
            a = range(iters)
            return iters

        args = (u32,)
        return_type = u8
        cres = compile_isolated(foo, args, return_type)
        typemap = cres.type_annotation.typemap
        # Argument "iters" must be uint32
        self.assertEqual(typemap['iters'], u32)


class TestUnify(unittest.TestCase):
    """
    Tests for type unification with a typing context.
    """

    int_unify = {
        ('uint8', 'uint8'): 'uint8',
        ('int8', 'int8'): 'int8',
        ('uint16', 'uint16'): 'uint16',
        ('int16', 'int16'): 'int16',
        ('uint32', 'uint32'): 'uint32',
        ('int32', 'int32'): 'int32',
        ('uint64', 'uint64'): 'uint64',
        ('int64', 'int64'): 'int64',

        ('int8', 'uint8'): 'int16',
        ('int8', 'uint16'): 'int32',
        ('int8', 'uint32'): 'int64',

        ('uint8', 'int32'): 'int32',
        ('uint8', 'uint64'): 'uint64',

        ('int16', 'int8'): 'int16',
        ('int16', 'uint8'): 'int16',
        ('int16', 'uint16'): 'int32',
        ('int16', 'uint32'): 'int64',
        ('int16', 'int64'): 'int64',
        ('int16', 'uint64'): 'float64',

        ('uint16', 'uint8'): 'uint16',
        ('uint16', 'uint32'): 'uint32',
        ('uint16', 'int32'): 'int32',
        ('uint16', 'uint64'): 'uint64',

        ('int32', 'int8'): 'int32',
        ('int32', 'int16'): 'int32',
        ('int32', 'uint32'): 'int64',
        ('int32', 'int64'): 'int64',

        ('uint32', 'uint8'): 'uint32',
        ('uint32', 'int64'): 'int64',
        ('uint32', 'uint64'): 'uint64',

        ('int64', 'int8'): 'int64',
        ('int64', 'uint8'): 'int64',
        ('int64', 'uint16'): 'int64',

        ('uint64', 'int8'): 'float64',
        ('uint64', 'int32'): 'float64',
        ('uint64', 'int64'): 'float64',
    }

    def assert_unify(self, aty, bty, expected):
        ctx = typing.Context()
        template = "{0}, {1} -> {2} != {3}"
        unified = ctx.unify_types(aty, bty)
        self.assertEqual(unified, expected,
                         msg=template.format(aty, bty, unified, expected))
        unified = ctx.unify_types(bty, aty)
        self.assertEqual(unified, expected,
                         msg=template.format(bty, aty, unified, expected))

    def assert_unify_failure(self, aty, bty):
        self.assert_unify(aty, bty, types.pyobject)

    def test_integer(self):
        ctx = typing.Context()
        for aty, bty in itertools.product(types.integer_domain,
                                          types.integer_domain):
            key = (str(aty), str(bty))
            try:
                expected = self.int_unify[key]
            except KeyError:
                expected = self.int_unify[key[::-1]]
            self.assert_unify(aty, bty, getattr(types, expected))

    def unify_number_pair_test(self, n):
        """
        Test all permutations of N-combinations of numeric types and ensure
        that the order of types in the sequence is irrelevant.
        """
        ctx = typing.Context()
        for tys in itertools.combinations(types.number_domain, n):
            res = [ctx.unify_types(*comb)
                   for comb in itertools.permutations(tys)]
            first_result = res[0]
            # Sanity check
            self.assertIsInstance(first_result, types.Number)
            # All results must be equal
            for other in res[1:]:
                self.assertEqual(first_result, other)

    def test_unify_number_pair(self):
        self.unify_number_pair_test(2)
        self.unify_number_pair_test(3)

    def test_none_to_optional(self):
        """
        Test unification of `none` and multiple number types to optional type
        """
        ctx = typing.Context()
        for tys in itertools.combinations(types.number_domain, 2):
            # First unify without none, to provide the control value
            tys = list(tys)
            expected = types.Optional(ctx.unify_types(*tys))
            results = [ctx.unify_types(*comb)
                       for comb in itertools.permutations(tys  + [types.none])]
            # All results must be equal
            for res in results:
                self.assertEqual(res, expected)

    def test_none(self):
        aty = types.none
        bty = types.none
        self.assert_unify(aty, bty, types.none)

    def test_optional(self):
        aty = types.Optional(i32)
        bty = types.none
        self.assert_unify(aty, bty, aty)
        aty = types.Optional(i32)
        bty = types.Optional(i64)
        self.assert_unify(aty, bty, bty)
        aty = types.Optional(i32)
        bty = i64
        self.assert_unify(aty, bty, types.Optional(i64))
        # Failure
        aty = types.Optional(i32)
        bty = types.Optional(types.len_type)
        self.assert_unify_failure(aty, bty)

    def test_tuple(self):
        aty = types.UniTuple(i32, 3)
        bty = types.UniTuple(i64, 3)
        self.assert_unify(aty, bty, types.UniTuple(i64, 3))
        # (Tuple, UniTuple) -> Tuple
        aty = types.UniTuple(i32, 2)
        bty = types.Tuple((i16, i64))
        self.assert_unify(aty, bty, types.Tuple((i32, i64)))
        # (Tuple, Tuple) -> Tuple
        aty = types.Tuple((i8, i16, i32))
        bty = types.Tuple((i32, i16, i8))
        self.assert_unify(aty, bty, types.Tuple((i32, i16, i32)))
        aty = types.Tuple((i8, i32))
        bty = types.Tuple((i32, i8))
        self.assert_unify(aty, bty, types.Tuple((i32, i32)))
        aty = types.Tuple((i8, i16))
        bty = types.Tuple((i16, i8))
        self.assert_unify(aty, bty, types.Tuple((i16, i16)))
        # Different number kinds
        aty = types.UniTuple(f64, 3)
        bty = types.UniTuple(c64, 3)
        self.assert_unify(aty, bty, types.UniTuple(c128, 3))
        # Tuples of tuples
        aty = types.UniTuple(types.Tuple((u32, f32)), 2)
        bty = types.UniTuple(types.Tuple((i16, f32)), 2)
        self.assert_unify(aty, bty,
                          types.UniTuple(types.Tuple((i64, f32)), 2))
        # Failures
        aty = types.UniTuple(i32, 1)
        bty = types.UniTuple(types.len_type, 1)
        self.assert_unify_failure(aty, bty)
        aty = types.UniTuple(i32, 1)
        bty = types.UniTuple(i32, 2)
        self.assert_unify_failure(aty, bty)
        aty = types.Tuple((i8, types.len_type))
        bty = types.Tuple((i32, i8))
        self.assert_unify_failure(aty, bty)

    def test_optional_tuple(self):
        # Unify to optional tuple
        aty = types.none
        bty = types.UniTuple(i32, 2)
        self.assert_unify(aty, bty, types.Optional(types.UniTuple(i32, 2)))
        aty = types.Optional(types.UniTuple(i16, 2))
        bty = types.UniTuple(i32, 2)
        self.assert_unify(aty, bty, types.Optional(types.UniTuple(i32, 2)))
        # Unify to tuple of optionals
        aty = types.Tuple((types.none, i32))
        bty = types.Tuple((i16, types.none))
        self.assert_unify(aty, bty, types.Tuple((types.Optional(i16),
                                                 types.Optional(i32))))
        aty = types.Tuple((types.Optional(i32), i64))
        bty = types.Tuple((i16, types.Optional(i8)))
        self.assert_unify(aty, bty, types.Tuple((types.Optional(i32),
                                                 types.Optional(i64))))

    def test_arrays(self):
        aty = types.Array(i32, 3, "C")
        bty = types.Array(i32, 3, "A")
        self.assert_unify(aty, bty, bty)
        aty = types.Array(i32, 3, "C")
        bty = types.Array(i32, 3, "F")
        self.assert_unify(aty, bty, types.Array(i32, 3, "A"))
        aty = types.Array(i32, 3, "C")
        bty = types.Array(i32, 3, "C", readonly=True)
        self.assert_unify(aty, bty, bty)
        aty = types.Array(i32, 3, "A")
        bty = types.Array(i32, 3, "C", readonly=True)
        self.assert_unify(aty, bty,
                          types.Array(i32, 3, "A", readonly=True))
        # Failures
        aty = types.Array(i32, 2, "C")
        bty = types.Array(i32, 3, "C")
        self.assert_unify_failure(aty, bty)
        aty = types.Array(i32, 2, "C")
        bty = types.Array(u32, 2, "C")
        self.assert_unify_failure(aty, bty)

    def test_list(self):
        aty = types.List(types.undefined)
        bty = types.List(i32)
        self.assert_unify(aty, bty, bty)
        aty = types.List(i16)
        bty = types.List(i32)
        self.assert_unify(aty, bty, bty)
        aty = types.List(types.Tuple([i32, i16]))
        bty = types.List(types.Tuple([i16, i64]))
        cty = types.List(types.Tuple([i32, i64]))
        self.assert_unify(aty, bty, cty)
        aty = types.List(i16)
        bty = types.List(types.Tuple([i16]))
        self.assert_unify_failure(aty, bty)


class TestTypeConversion(CompatibilityTestMixin, unittest.TestCase):
    """
    Test for conversion between types with a typing context.
    """

    def assert_can_convert(self, aty, bty, expected):
        ctx = typing.Context()
        got = ctx.can_convert(aty, bty)
        self.assertEqual(got, expected)

    def assert_cannot_convert(self, aty, bty):
        ctx = typing.Context()
        got = ctx.can_convert(aty, bty)
        self.assertIsNone(got)

    def test_convert_number_types(self):
        # Check that Context.can_convert() is compatible with the default
        # number conversion rules registered in the typeconv module
        # (which is used internally by the C _Dispatcher object).
        ctx = typing.Context()
        self.check_number_compatibility(ctx.can_convert)

    def test_tuple(self):
        aty = types.UniTuple(i32, 3)
        bty = types.UniTuple(i64, 3)
        self.assert_can_convert(aty, aty, Conversion.exact)
        self.assert_can_convert(aty, bty, Conversion.promote)
        aty = types.UniTuple(i32, 3)
        bty = types.UniTuple(f64, 3)
        self.assert_can_convert(aty, bty, Conversion.safe)
        aty = types.Tuple((i32, i32))
        bty = types.Tuple((i32, i64))
        self.assert_can_convert(aty, bty, Conversion.promote)
        # Failures
        aty = types.UniTuple(i64, 3)
        bty = types.UniTuple(types.none, 3)
        self.assert_cannot_convert(aty, bty)
        aty = types.UniTuple(i64, 2)
        bty = types.UniTuple(i64, 3)

    def test_arrays(self):
        # Different layouts
        aty = types.Array(i32, 3, "C")
        bty = types.Array(i32, 3, "A")
        self.assert_can_convert(aty, bty, Conversion.safe)
        aty = types.Array(i32, 2, "C")
        bty = types.Array(i32, 2, "F")
        self.assert_cannot_convert(aty, bty)
        # Different mutabilities
        aty = types.Array(i32, 3, "C")
        bty = types.Array(i32, 3, "C", readonly=True)
        self.assert_can_convert(aty, aty, Conversion.exact)
        self.assert_can_convert(bty, bty, Conversion.exact)
        self.assert_can_convert(aty, bty, Conversion.safe)
        self.assert_cannot_convert(bty, aty)
        # Various failures
        aty = types.Array(i32, 2, "C")
        bty = types.Array(i32, 3, "C")
        self.assert_cannot_convert(aty, bty)
        aty = types.Array(i32, 2, "C")
        bty = types.Array(i64, 2, "C")
        self.assert_cannot_convert(aty, bty)

    def test_optional(self):
        aty = types.int32
        bty = types.Optional(i32)
        self.assert_can_convert(types.none, bty, Conversion.promote)
        self.assert_can_convert(aty, bty, Conversion.promote)
        self.assert_cannot_convert(bty, types.none)
        self.assert_can_convert(bty, aty, Conversion.safe)  # XXX ???
        # Optional array
        aty = types.Array(i32, 2, "C")
        bty = types.Optional(aty)
        self.assert_can_convert(types.none, bty, Conversion.promote)
        self.assert_can_convert(aty, bty, Conversion.promote)
        self.assert_can_convert(bty, aty, Conversion.safe)
        aty = types.Array(i32, 2, "C")
        bty = types.Optional(aty.copy(layout="A"))
        self.assert_can_convert(aty, bty, Conversion.safe)  # C -> A
        self.assert_cannot_convert(bty, aty)                # A -> C
        aty = types.Array(i32, 2, "C")
        bty = types.Optional(aty.copy(layout="F"))
        self.assert_cannot_convert(aty, bty)
        self.assert_cannot_convert(bty, aty)


class TestResolveOverload(unittest.TestCase):
    """
    Tests for typing.Context.resolve_overload().
    """

    def assert_resolve_overload(self, cases, args, expected):
        ctx = typing.Context()
        got = ctx.resolve_overload("foo", cases, args, {})
        self.assertEqual(got, expected)

    def test_non_ambiguous_match(self):
        def check(args, expected):
            self.assert_resolve_overload(cases, args, expected)
            # Order shouldn't matter here
            self.assert_resolve_overload(cases[::-1], args, expected)

        cases = [i8(i8, i8), i32(i32, i32), f64(f64, f64)]
        # Exact match
        check((i8, i8), cases[0])
        check((i32, i32), cases[1])
        check((f64, f64), cases[2])
        # "Promote" conversion
        check((i8, i16), cases[1])
        check((i32, i8), cases[1])
        check((i32, i8), cases[1])
        check((f32, f32), cases[2])
        # "Safe" conversion
        check((u32, u32), cases[2])
        # "Unsafe" conversion
        check((i64, i64), cases[2])

    def test_ambiguous_match(self):
        # When the best match is ambiguous (there is a tie), the first
        # best case in original sequence order should be returned.
        def check(args, expected, expected_reverse):
            self.assert_resolve_overload(cases, args, expected)
            self.assert_resolve_overload(cases[::-1], args, expected_reverse)

        cases = [i16(i16, i16), i32(i32, i32), f64(f64, f64)]
        # Two "promote" conversions
        check((i8, i8), cases[0], cases[1])
        # Two "safe" conversions
        check((u16, u16), cases[1], cases[2])

        cases = [i32(i32, i32), f32(f32, f32)]
        # Two "unsafe" conversions
        check((u32, u32), cases[0], cases[1])

    def test_ambiguous_error(self):
        ctx = typing.Context()
        cases = [i16(i16, i16), i32(i32, i32)]
        with self.assertRaises(TypeError) as raises:
            ctx.resolve_overload("foo", cases, (i8, i8), {},
                                 allow_ambiguous=False)
        self.assertEqual(str(raises.exception).splitlines(),
                         ["Ambiguous overloading for foo (int8, int8):",
                          "(int16, int16) -> int16",
                          "(int32, int32) -> int32",
                          ])


class TestUnifyUseCases(unittest.TestCase):
    """
    Concrete cases where unification would fail.
    """

    @staticmethod
    def _actually_test_complex_unify():
        def pyfunc(a):
            res = 0.0
            for i in range(len(a)):
                res += a[i]
            return res

        argtys = [types.Array(c128, 1, 'C')]
        cres = compile_isolated(pyfunc, argtys)
        return (pyfunc, cres)

    def test_complex_unify_issue599(self):
        pyfunc, cres = self._actually_test_complex_unify()
        arg = np.array([1.0j])
        cfunc = cres.entry_point
        self.assertEqual(cfunc(arg), pyfunc(arg))

    def test_complex_unify_issue599_multihash(self):
        """
        Test issue #599 for multiple values of PYTHONHASHSEED.
        """
        env = os.environ.copy()
        for seedval in (1, 2, 1024):
            env['PYTHONHASHSEED'] = str(seedval)
            subproc = subprocess.Popen(
                [sys.executable, '-c',
                 'import numba.tests.test_typeinfer as test_mod\n' +
                 'test_mod.TestUnifyUseCases._actually_test_complex_unify()'],
                env=env)
            subproc.wait()
            self.assertEqual(subproc.returncode, 0, 'Child process failed.')

    def test_int_tuple_unify(self):
        """
        Test issue #493
        """
        def foo(an_int32, an_int64):
            a = an_int32, an_int32
            while True:  # infinite loop
                a = an_int32, an_int64
            return a

        args = (i32, i64)
        # Check if compilation is successful
        cres = compile_isolated(foo, args)


def issue_797(x0, y0, x1, y1, grid):
    nrows, ncols = grid.shape

    dx = abs(x1 - x0)
    dy = abs(y1 - y0)

    sx = 0
    if x0 < x1:
        sx = 1
    else:
        sx = -1
    sy = 0
    if y0 < y1:
        sy = 1
    else:
        sy = -1

    err = dx - dy

    while True:
        if x0 == x1 and y0 == y1:
            break

        if 0 <= x0 < nrows and 0 <= y0 < ncols:
            grid[x0, y0] += 1

        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


def issue_1080(a, b):
    if not a:
        return True
    return b


def list_unify_usecase1(n):
    res = 0
    x = []
    if n < 10:
        x.append(np.int32(n))
    else:
        for i in range(n):
            x.append(np.int64(i))
    x.append(5.0)

    # Note `i` and `j` may have different types (int64 vs. int32)
    for j in range(len(x)):
        res += j * x[j]
    for val in x:
        res += int(val) & len(x)
    while len(x) > 0:
        res += x.pop()
    return res

def list_unify_usecase2(n):
    res = []
    for i in range(n):
        if i & 1:
            res.append((i, 1.0))
        else:
            res.append((2.0, i))
    res.append((123j, 42))
    return res


class TestMiscIssues(TestCase):

    def test_issue_797(self):
        """https://github.com/numba/numba/issues/797#issuecomment-58592401

        Undeterministic triggering of tuple coercion error
        """
        foo = jit(nopython=True)(issue_797)
        g = np.zeros(shape=(10, 10), dtype=np.int32)
        foo(np.int32(0), np.int32(0), np.int32(1), np.int32(1), g)

    def test_issue_1080(self):
        """https://github.com/numba/numba/issues/1080

        Erroneous promotion of boolean args to int64
        """
        foo = jit(nopython=True)(issue_1080)
        foo(True, False)

    def test_list_unify1(self):
        """
        Exercise back-propagation of refined list type.
        """
        pyfunc = list_unify_usecase1
        cfunc = jit(nopython=True)(pyfunc)
        for n in [5, 100]:
            res = cfunc(n)
            self.assertPreciseEqual(res, pyfunc(n))

    def test_list_unify2(self):
        pyfunc = list_unify_usecase2
        cfunc = jit(nopython=True)(pyfunc)
        res = cfunc(3)
        # NOTE: the types will differ (Numba returns a homogenous list with
        # converted values).
        self.assertEqual(res, pyfunc(3))


if __name__ == '__main__':
    unittest.main()
