from algopy import (
    Account,
    Application,
    ARC4Contract,
    Asset,
    BigUInt,
    BoxMap,
    Bytes,
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
B = BigUInt


@subroutine
def get_tm2_net_amt(amt: UInt64) -> UInt64:
    return amt - (UInt64(30) * amt // UInt64(10000))


@subroutine
def get_tinyman_algo_price_for_asset(
    tm2_app_id: UInt64,
    tm2_lp_addr: Bytes,
    farm_amount: UInt64,
) -> UInt64:
    tma = Account(tm2_lp_addr)
    tm2 = Application(tm2_app_id)
    aid1, exists1 = op.AppLocal.get_ex_uint64(tma, tm2, b"asset_1_id")
    a1, exists2 = op.AppLocal.get_ex_uint64(tma, tm2, b"asset_1_reserves")
    a2, exists3 = op.AppLocal.get_ex_uint64(tma, tm2, b"asset_2_reserves")
    custom.ensure(exists1 and exists2 and exists3, S("ERR:TM STT"))
    if aid1 != UInt64(0):
        return (
            a2
            - UInt64(1)
            - op.btoi((B(a1 * a2) // B(a1 + get_tm2_net_amt(farm_amount))).bytes)
        )
    else:
        return (
            a1
            - UInt64(1)
            - op.btoi((B(a1 * a2) // B(a2 + get_tm2_net_amt(farm_amount))).bytes)
        )


# required block range availability to sample avg round time. constant
MIN_ROUND_SAMPLE = 500

# default value for minimum allowed duration in blocks
DEFAULT_MIN_DURATION_BLOCKS = 30

# default value max farm duration in days.
# projected from expected block production.
# bound by minimum value above
DEFAULT_MAX_DURATION_DAYS = 45

# expressed as multiples of min txn fee
PLATFORM_FEE_PER_BLOCK = 97  # 0.097 ALGO
TXN_FEE_PER_BLOCK = 3  # 0.003 ALGO // 3 txns per payout: 1 farm reward axfer, 1 ds swap app call, 1 send IX reward drop
IX_REWARDS_PER_BLOCK = (
    100  # 0.1 ALGO // caller receives 100 min txn fees as incentivized execution reward
)


class AlgoCost(arc4.Struct):
    total_cost: arc4.UInt64
    optin_cost: arc4.UInt64
    box_cost: arc4.UInt64
    platform_cost: arc4.UInt64
    ix_cost: arc4.UInt64
    txn_fee_cost: arc4.UInt64


class AlgoCostAndMaxDuration(arc4.Struct):
    total_cost: arc4.UInt64
    optin_cost: arc4.UInt64
    box_cost: arc4.UInt64
    platform_cost: arc4.UInt64
    ix_cost: arc4.UInt64
    txn_fee_cost: arc4.UInt64
    max_duration: arc4.UInt64


class APRBreakdown(arc4.Struct):
    balance: arc4.UInt64
    staked: arc4.UInt64
    current_block_bonus: arc4.UInt64
    current_avg_block_payout: arc4.UInt64
    current_farm_amount: arc4.UInt64
    current_farm_amount_algo: arc4.UInt64
    avg_round_time: arc4.UInt64
    online_stake: arc4.UInt64
    expected_yearly_blocks: arc4.UInt64
    projected_apr_bps: arc4.UInt64


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
        self.global_remaining_blocks = UInt64(0)

        self.max_duration_days = UInt64(DEFAULT_MAX_DURATION_DAYS)
        self.min_duration_blocks = UInt64(DEFAULT_MIN_DURATION_BLOCKS)

        self.ix_pb = UInt64(IX_REWARDS_PER_BLOCK)
        self.plat_fee_pb = UInt64(PLATFORM_FEE_PER_BLOCK)
        self.txn_fee_pb = UInt64(TXN_FEE_PER_BLOCK)

    @arc4.baremethod(allow_actions=("UpdateApplication",))
    def update(self) -> None:
        self.ensure_manager_caller()

    @arc4.baremethod(allow_actions=("DeleteApplication",))
    def delete(self) -> None:
        self.ensure_manager_caller()

    @abimethod(readonly=True)
    def project_apr(
        self,
        recipient_app_id: Application,
        # override_farm_amount: UInt64,
    ) -> APRBreakdown:
        tm2_app_id, exists2 = op.AppGlobal.get_ex_uint64(
            recipient_app_id, b"tm2_app_id"
        )
        tm2_lp_addr, exists3 = op.AppGlobal.get_ex_bytes(recipient_app_id, b"lp_id")

        asa_id, exists1 = op.AppGlobal.get_ex_uint64(recipient_app_id, b"asa_id")
        staked, exists4 = op.AppGlobal.get_ex_uint64(recipient_app_id, b"staked")
        custom.ensure(exists1 and exists2 and exists3 and exists4, S("ERR:DS STT"))

        # balance is staked+fees. Use this to calculate blocks (nom in % of online)
        balance = recipient_app_id.address.balance
        total_online_stake = BigUInt(op.online_stake())

        current_block_rewards = op.Block.blk_bonus(Txn.first_valid - UInt64(1))
        current_avg_block_payout = round_time.get_block_rewards()

        rt_fraction = round_time.get_round_time(UInt64(MIN_ROUND_SAMPLE))
        avg_round_time = UInt64(10000) * rt_fraction.dt // rt_fraction.dr
        global_yearly_blocks_produced = BigUInt(
            UInt64(86400) * UInt64(365) * rt_fraction.dr // rt_fraction.dt
        )

        own_yearly_blocks_produced = (
            global_yearly_blocks_produced * balance // total_online_stake
        )
        farm_amount = UInt64(0)
        if recipient_app_id in self.farms:
            farm_amount = self.farms[recipient_app_id].amount_per_block.native

        farm_amount_algo = (
            get_tinyman_algo_price_for_asset(tm2_app_id, tm2_lp_addr, farm_amount)
            if farm_amount > UInt64(0)
            else UInt64(0)
        )

        rewards = (
            current_block_rewards + farm_amount_algo
        ) * own_yearly_blocks_produced
        projected_apr_bps = UInt64(10000) * rewards // staked

        return APRBreakdown(
            balance=arc4.UInt64(balance),
            staked=arc4.UInt64(staked),
            current_block_bonus=arc4.UInt64(current_block_rewards),
            current_avg_block_payout=arc4.UInt64(current_avg_block_payout),
            current_farm_amount=arc4.UInt64(farm_amount),
            current_farm_amount_algo=arc4.UInt64(farm_amount_algo),
            avg_round_time=arc4.UInt64(avg_round_time),
            online_stake=arc4.UInt64(total_online_stake),
            expected_yearly_blocks=arc4.UInt64(own_yearly_blocks_produced),
            projected_apr_bps=arc4.UInt64(projected_apr_bps),
        )

    @subroutine
    def calculate_algo_cost(
        self, recipient_app: Application, farm_asset: Asset, duration_blocks: UInt64
    ) -> AlgoCost:
        optin_mbr = (
            UInt64(0)
            if Global.current_application_address.is_opted_in(farm_asset)
            else Global.asset_opt_in_min_balance
        )
        box_mbr = (
            UInt64(0) if recipient_app in self.farms else UInt64(5 * 8 * 400 + 2500)
        )  # box size is 1+4 uint64s

        ix_cost = self.get_ix_rewards_per_block() * duration_blocks
        txn_fee_cost = self.get_txn_fee_per_block() * duration_blocks
        platform_cost = self.get_platform_fee_per_block() * duration_blocks
        total_cost = ix_cost + txn_fee_cost + platform_cost

        return AlgoCost(
            total_cost=arc4.UInt64(total_cost),
            box_cost=arc4.UInt64(box_mbr),
            optin_cost=arc4.UInt64(optin_mbr),
            txn_fee_cost=arc4.UInt64(txn_fee_cost),
            ix_cost=arc4.UInt64(ix_cost),
            platform_cost=arc4.UInt64(platform_cost),
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
        cost = self.calculate_algo_cost(recipient_app, farm_asset, duration_blocks)
        return AlgoCostAndMaxDuration(
            total_cost=cost.total_cost,
            box_cost=cost.box_cost,
            optin_cost=cost.optin_cost,
            txn_fee_cost=cost.txn_fee_cost,
            ix_cost=cost.ix_cost,
            platform_cost=cost.platform_cost,
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
            ).total_cost.native,
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
        self.txn_fuel = self.txn_fuel + self.get_spend_per_block() * duration_blocks
        self.global_remaining_blocks = self.global_remaining_blocks + duration_blocks

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
            ).total_cost.native,
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
        self.txn_fuel = self.txn_fuel + self.get_spend_per_block() * duration_blocks

        self.global_remaining_blocks = self.global_remaining_blocks + duration_blocks

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

        # pay out reward
        send.axfer(
            Asset(state.farm_asset.native),
            recipient_app.address,
            state.amount_per_block.native,
            Global.min_txn_fee,
        )
        txn_fuel_spent = txn_fuel_spent + Global.min_txn_fee

        # send ix reward
        send.algo_pay(Txn.sender, self.get_ix_rewards_per_block(), Global.min_txn_fee)
        txn_fuel_spent = (
            txn_fuel_spent + Global.min_txn_fee + self.get_ix_rewards_per_block()
        )

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
        self.global_remaining_blocks = self.global_remaining_blocks - 1

    @abimethod
    def noop(self) -> None:
        return

    @abimethod
    def withdraw_fees(self, amount: UInt64) -> None:
        self.ensure_manager_caller()
        locked_balance = Global.current_application_address.min_balance + (
            self.global_remaining_blocks * self.get_spend_per_block()
        )
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
            box_name = Application(box_names[k].native)
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
        return (
            self.get_txn_fee_per_block()
            + self.get_ix_rewards_per_block()
            + self.get_farm_algo_cost_per_block()
        )

    @subroutine
    def get_spend_per_block(self) -> UInt64:
        return self.get_txn_fee_per_block() + self.get_ix_rewards_per_block()

    @subroutine
    def get_ix_rewards_per_block(self) -> UInt64:
        return self.ix_pb * Global.min_txn_fee

    @subroutine
    def get_platform_fee_per_block(self) -> UInt64:
        return self.plat_fee_pb * Global.min_txn_fee

    @subroutine
    def get_txn_fee_per_block(self) -> UInt64:
        return self.txn_fee_pb * Global.min_txn_fee
