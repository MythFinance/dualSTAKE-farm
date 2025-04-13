from typing import NamedTuple

from algopy import (
    String,
    Txn,
    UInt64,
    op,
    subroutine,
)

from . import custom, math

S = String


class RoundTimeFraction(NamedTuple):
    dt: UInt64
    dr: UInt64


@subroutine
def get_round_time(min_round_sample: UInt64) -> RoundTimeFraction:
    first_accessible = math.safe_subtract(Txn.last_valid, UInt64(1001), UInt64(1))
    last_accessible = Txn.first_valid - UInt64(1)
    # TODO fallback for localnet? if first_accessible == 1 then skip?
    if first_accessible > UInt64(1):
        custom.ensure(
            last_accessible - first_accessible >= min_round_sample,
            S("ERR:BLK RNGE"),
        )
    block_delta = last_accessible - first_accessible
    ts_delta = op.Block.blk_timestamp(last_accessible) - op.Block.blk_timestamp(
        first_accessible
    )
    return RoundTimeFraction(dt=ts_delta, dr=block_delta)
