from algopy import (
    UInt64,
    subroutine,
)


@subroutine
def max(a: UInt64, b: UInt64) -> UInt64:
    return a if a > b else b


@subroutine
def safe_subtract(a: UInt64, b: UInt64, default: UInt64) -> UInt64:
    return a - b if a > b else default
