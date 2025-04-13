from typing import cast

from algopy import (
    Application,
    ARC4Contract,
    Asset,
    BigUInt,
    BoxMap,
    Global,
    StateTotals,
    String,
    Txn,
    UInt64,
    arc4,
    log,
    op,
    subroutine,
    urange,
)
from algopy.arc4 import abi_call, abimethod

from ..common import custom, math, round_time, send, validate

S = String

# required block range availability to sample avg round time. constant
MIN_ROUND_SAMPLE = 500

# default value for minimum allowed duration in blocks
DEFAULT_MIN_DURATION_BLOCKS = 30

# default value max farm duration in days.
# projected from expected block production.
# bound by minimum value above
DEFAULT_MAX_DURATION_DAYS = 45

FARM_ALGO_COST_PER_BLOCK = 10  # * min_txn_fee, 3 for platform fees + ...
TXN_FUEL_PER_BLOCK = 7  # * min_txn_fee. 2 for asa/app call, + 1 to send IX reward +
IX_REWARDS_PER_BLOCK = 4  # caller receives 4 as IX reward


class AlgoCost(arc4.Struct):
    algo_cost: arc4.UInt64
    optin_cost: arc4.UInt64
    box_cost: arc4.UInt64
    farm_cost: arc4.UInt64


class AlgoCostAndMaxDuration(arc4.Struct):
    algo_cost: arc4.UInt64
    optin_cost: arc4.UInt64
    box_cost: arc4.UInt64
    farm_cost: arc4.UInt64
    max_duration: arc4.UInt64


class FarmState(arc4.Struct):
    farm_asset: arc4.UInt64
    amount_per_block: arc4.UInt64
    remaining_duration_blocks: arc4.UInt64
    last_block_paid: arc4.UInt64


class DualstakeFarm(
    ARC4Contract,
    avm_version=11,
    state_totals=StateTotals(global_uints=40, global_bytes=24),
):
    def __init__(self) -> None:
        self.manager = Txn.sender
        self.farms = BoxMap(Application, FarmState, key_prefix=b"")
        self.txn_fuel = UInt64(0)
        self.max_duration_days = UInt64(DEFAULT_MAX_DURATION_DAYS)
        self.min_duration_blocks = UInt64(DEFAULT_MIN_DURATION_BLOCKS)

    @arc4.baremethod(allow_actions=("UpdateApplication",))
    def update(self) -> None:
        self.ensure_manager_caller()

    @arc4.baremethod(allow_actions=("DeleteApplication",))
    def delete(self) -> None:
        self.ensure_manager_caller()

    @subroutine
    def calculate_algo_cost(
        self, recipient_app: Application, farm_asset: Asset, duration_blocks: UInt64
    ) -> AlgoCost:
        optin_mbr = (
            UInt64(0)
            if Global.current_application_address.is_opted_in(farm_asset)
            else Global.asset_opt_in_min_balance
        )
        log(optin_mbr)
        box_mbr = (
            UInt64(0) if recipient_app in self.farms else UInt64(5 * 8 * 400 + 2500)
        )  # box size is 1+4 uint64s
        log(box_mbr)
        farm_costs = self.get_farm_algo_cost_per_block() * duration_blocks
        log(farm_costs)
        return AlgoCost(
            algo_cost=arc4.UInt64(optin_mbr + box_mbr + farm_costs),
            farm_cost=arc4.UInt64(farm_costs),
            box_cost=arc4.UInt64(box_mbr),
            optin_cost=arc4.UInt64(optin_mbr),
        )

    @abimethod(readonly=True)
    def get_algo_cost(
        self, recipient_app: Application, farm_asset: Asset, duration_blocks: UInt64
    ) -> AlgoCost:
        return self.calculate_algo_cost(recipient_app, farm_asset, duration_blocks)

    @abimethod(readonly=True)
    def get_algo_cost_and_max_duration(
        self, recipient_app: Application, farm_asset: Asset, duration_blocks: UInt64
    ) -> AlgoCostAndMaxDuration:
        algo_cost_struct = self.calculate_algo_cost(
            recipient_app, farm_asset, duration_blocks
        )
        return AlgoCostAndMaxDuration(
            algo_cost=algo_cost_struct.algo_cost,
            box_cost=algo_cost_struct.box_cost,
            optin_cost=algo_cost_struct.optin_cost,
            farm_cost=algo_cost_struct.farm_cost,
            max_duration=arc4.UInt64(self.get_max_duration(recipient_app)),
        )

    @subroutine
    def get_max_duration(self, recipient_app: Application) -> UInt64:
        """
        Get max allowed duration for a contract in days.
        Estimates the blocks to be produced in the next 45 days (MAX_DURATION_DAYS).
        """
        ds_balance = BigUInt(recipient_app.address.balance)
        total_online_stake = BigUInt(op.online_stake())
        # round_time = (dt == time2 - time1) / (dr == block2 - block1)
        rt_fraction = round_time.get_round_time(UInt64(MIN_ROUND_SAMPLE))
        # blocks produced = 45 days in seconds / round_time
        blocks_produced = BigUInt(
            UInt64(86400)
            * UInt64(DEFAULT_MAX_DURATION_DAYS)
            * rt_fraction.dr
            // rt_fraction.dt
        )
        # max duration = percentage_of_stake * blocks produced in 45 days
        # = own_stake * blocks_produced / total_stake
        max_duration = ds_balance * blocks_produced // total_online_stake
        return math.max(
            UInt64(DEFAULT_MIN_DURATION_BLOCKS), op.btoi(max_duration.bytes)
        )

    @subroutine
    def validate_duration(
        self, recipient_app: Application, duration_blocks: UInt64
    ) -> None:
        allowed_duration = self.get_max_duration(recipient_app)
        if allowed_duration < duration_blocks:
            log(allowed_duration)
            log("ERR:DURATION")
            op.err()

    @abimethod()
    def create_farm(
        self,
        recipient_app: Application,
        farm_asset: Asset,
        amount_per_block: UInt64,
        duration_blocks: UInt64,
    ) -> None:
        # reject if farm exists already
        custom.ensure(recipient_app not in self.farms, S("ERR:EXISTS"))

        custom.ensure(Txn.group_index > 0, S("ERR:NO PAY"))

        # validate ALGO payment. positioned before so it can cover optin and box MBR
        validate.payment_amount_exact(
            Txn.group_index - UInt64(1),  # previous txn
            self.calculate_algo_cost(
                recipient_app, farm_asset, duration_blocks
            ).algo_cost.native,
        )

        # validate ASA deposit. positioned after app call so we can opt in if needed
        # don't do as I do, if you use this pattern you can get exploited if another method validates an asa payment at (-1)
        # and if you do do as I do, ensure all your axfers are expected at +1
        validate.axfer_amount_exact(
            Txn.group_index + UInt64(1),  # next txn
            farm_asset,
            amount_per_block * duration_blocks,
        )

        self.validate_duration(recipient_app, duration_blocks)

        # Check recipient app state
        recipient_asa_id, exists = op.AppGlobal.get_ex_uint64(recipient_app, b"asa_id")
        custom.ensure(recipient_asa_id == farm_asset.id, S("ERR:APP ASA"))

        log(self.get_max_duration(recipient_app))

        # optin if needed
        if not Global.current_application_address.is_opted_in(farm_asset):
            send.optin(farm_asset, UInt64(0))

        # create farm box entry
        self.farms[recipient_app] = FarmState(
            farm_asset=arc4.UInt64(farm_asset.id),
            amount_per_block=arc4.UInt64(amount_per_block),
            remaining_duration_blocks=arc4.UInt64(duration_blocks),
            last_block_paid=arc4.UInt64(Global.round + 1),
        )

        # add to global txn fuel
        self.txn_fuel = self.txn_fuel + self.get_txn_fuel_per_block() * duration_blocks

    @abimethod
    def extend_duration_blocks(
        self,
        recipient_app: Application,
        duration_blocks: UInt64,
    ) -> None:
        custom.ensure(recipient_app in self.farms, S("ERR:NO FARM"))

        state = self.farms[recipient_app].copy()
        farm_asset = Asset(state.farm_asset.native)

        # validate ALGO and ASA payments. keeping create_farm before/after structure for simplicity
        validate.payment_amount_exact(
            Txn.group_index - UInt64(1),  # previous txn
            self.calculate_algo_cost(
                recipient_app, farm_asset, duration_blocks
            ).algo_cost.native,
        )

        validate.axfer_amount_exact(
            Txn.group_index + UInt64(1),  # next txn
            farm_asset,
            state.amount_per_block.native * duration_blocks,
        )

        self.validate_duration(
            recipient_app, state.remaining_duration_blocks.native + duration_blocks
        )

        # adjust remaining blocks in state
        state.remaining_duration_blocks = arc4.UInt64(
            state.remaining_duration_blocks.native + duration_blocks
        )
        # save state
        self.farms[recipient_app] = state.copy()

        # adjust txn fuel remaining
        self.txn_fuel = self.txn_fuel + self.get_txn_fuel_per_block() * duration_blocks

    @abimethod
    def extend_amount_per_block(
        self,
        recipient_app: Application,
        amount_per_block: UInt64,
    ) -> None:
        custom.ensure(recipient_app in self.farms, S("ERR:NO FARM"))

        state = self.farms[recipient_app].copy()
        farm_asset = Asset(state.farm_asset.native)

        validate.axfer_amount_exact(
            Txn.group_index + UInt64(1),  # next txn
            farm_asset,
            amount_per_block * state.remaining_duration_blocks.native,
        )

        # adjust amount per block in state
        state.amount_per_block = arc4.UInt64(
            state.amount_per_block.native + amount_per_block
        )
        # save state
        self.farms[recipient_app] = state.copy()

    @abimethod()
    def payout(
        self, recipient_app: Application, block_round: UInt64, call_swap: arc4.Bool
    ) -> None:
        # ensure farm exists
        custom.ensure(recipient_app in self.farms, S("ERR:NO FARM"))

        # load farm state
        state = self.farms[recipient_app].copy()

        # ensure we have remaining blocks to pay out in this farm
        # if not, delete farm state and return
        if state.remaining_duration_blocks == 0:
            del self.farms[recipient_app]
            # TODO Emit event?
            log("expired")
            return

        # ensure our block is after the last block we have paid
        custom.ensure(block_round > state.last_block_paid, S("ERR:PAST"))

        # ensure app escrow produced the block
        custom.ensure(
            op.Block.blk_proposer(block_round) == recipient_app.address,
            S("ERR:NOT BLK PROP"),
        )

        # track spent txn costs to subtract from global
        txn_fuel_spent = UInt64(0)

        # call swap if needed
        if call_swap:
            abi_call(
                "swap_or_fail()void",
                app_id=recipient_app,
                fee=Global.min_txn_fee,
            )

        # subtract txn fuel regardless of call swap or not
        txn_fuel_spent = txn_fuel_spent + Global.min_txn_fee

        txn_fuel_spent = txn_fuel_spent + Global.min_txn_fee
        # pay out reward
        send.axfer(
            Asset(state.farm_asset.native),
            recipient_app.address,
            state.amount_per_block.native,
            Global.min_txn_fee,
        )

        # send ix reward
        txn_fuel_spent = (
            txn_fuel_spent + Global.min_txn_fee + self.get_ix_rewards_per_block()
        )
        send.algo_pay(Txn.sender, self.get_ix_rewards_per_block(), Global.min_txn_fee)

        # update box state
        state.last_block_paid = arc4.UInt64(block_round)
        state.remaining_duration_blocks = arc4.UInt64(
            state.remaining_duration_blocks.native - UInt64(1)
        )
        if state.remaining_duration_blocks == 0:
            del self.farms[recipient_app]
        else:
            self.farms[recipient_app] = state.copy()

        # update global txn fuel state
        self.txn_fuel = self.txn_fuel - txn_fuel_spent

    @abimethod
    def noop(self) -> None:
        return

    @abimethod
    def withdraw_fees(self, amount: UInt64) -> None:
        self.ensure_manager_caller()
        locked_balance = Global.current_application_address.min_balance + self.txn_fuel
        custom.ensure(
            locked_balance + amount <= Global.current_application_address.balance,
            S("ERR:OVER"),
        )
        send.algo_pay(
            Txn.sender,
            amount,
            UInt64(0),
        )

    @abimethod
    def optout(self, asset: Asset) -> None:
        self.ensure_manager_caller()
        custom.ensure(
            Global.current_application_address.is_opted_in(asset), S("ERR:NOT OPTED")
        )
        custom.ensure(
            asset.balance(Global.current_application_address) == 0, S("ERR:BALANCE")
        )
        send.axfer_closeout(asset, self.manager, UInt64(0))

    @abimethod
    def update_max_duration_days(self, max_duration: UInt64) -> None:
        self.ensure_manager_caller()
        self.max_duration_days = max_duration

    @abimethod
    def update_min_duration_blocks(self, min_duration: UInt64) -> None:
        self.ensure_manager_caller()
        self.min_duration_blocks = min_duration

    @abimethod(readonly=True)
    def get_state(self, recipient_app: Application) -> FarmState:
        return self.farms[recipient_app]

    @abimethod(readonly=True)
    def log_states(self, box_names: arc4.DynamicArray[arc4.UInt64]) -> None:
        for k in urange(box_names.length):
            box_name = cast(Application, box_names[k])
            if box_name in self.farms:
                log(self.farms[box_name])
            else:
                log(FarmState.from_bytes(b""))

    @abimethod(readonly=True)
    def log_block_proposers(self, start_round: UInt64, end_round: UInt64) -> None:
        for rnd in urange(start_round, end_round + 1):
            log(op.Block.blk_proposer(rnd))

    @subroutine
    def ensure_manager_caller(self) -> None:
        custom.ensure(Txn.sender == self.manager, S("ERR:UNAUTH"))

    @subroutine
    def get_farm_algo_cost_per_block(self) -> UInt64:
        return UInt64(FARM_ALGO_COST_PER_BLOCK) * Global.min_txn_fee

    @subroutine
    def get_txn_fuel_per_block(self) -> UInt64:
        return UInt64(TXN_FUEL_PER_BLOCK) * Global.min_txn_fee

    @subroutine
    def get_ix_rewards_per_block(self) -> UInt64:
        return UInt64(IX_REWARDS_PER_BLOCK) * Global.min_txn_fee
