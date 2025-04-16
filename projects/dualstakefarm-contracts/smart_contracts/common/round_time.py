from typing import NamedTuple

from algopy import (
    String,
    Txn,
    UInt64,
    op,
    subroutine,
    urange,
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


@subroutine
def get_block_rewards() -> UInt64:
    first_accessible = math.safe_subtract(Txn.last_valid, UInt64(1001), UInt64(1))
    last_accessible = Txn.first_valid - UInt64(1)
    delta = last_accessible - first_accessible
    bonus = op.Block.blk_bonus(last_accessible)
    num_payouts = UInt64(0)
    sum_payouts = UInt64(0)
    for rnd_delta in urange(delta):
        rnd = last_accessible - rnd_delta
        if op.Block.blk_proposer_payout(rnd) > UInt64(0):
            num_payouts = num_payouts + UInt64(1)
            sum_payouts = sum_payouts + op.Block.blk_proposer_payout(rnd)
    avg_payout_or_bonus = (
        sum_payouts // num_payouts if num_payouts > UInt64(0) else bonus
    )
    return avg_payout_or_bonus
