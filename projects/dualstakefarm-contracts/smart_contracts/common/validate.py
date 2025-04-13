from algopy import Asset, Global, String, UInt64, gtxn, subroutine

from . import custom

S = String


@subroutine
def asset(asset: Asset, err_msg: String) -> None:
    # Proxy for asset existence check TODO
    # TODO This will probably throw a non-custom error if we access .total of not-an-asset
    custom.ensure(asset.total > 0, err_msg)


@subroutine
def axfer(axfer_txn_idx: UInt64, expected_asset: Asset) -> UInt64:
    axfer_txn = gtxn.AssetTransferTransaction(axfer_txn_idx)
    custom.ensure(axfer_txn.xfer_asset == expected_asset, S("ERR:AXFER ID"))
    custom.ensure(
        axfer_txn.asset_receiver == Global.current_application_address,
        S("ERR:AXFER RCV"),
    )
    return axfer_txn.asset_amount


@subroutine
def axfer_amount_exact(
    axfer_txn_id: UInt64, expected_asset: Asset, expected_amount: UInt64
) -> None:
    custom.ensure(
        axfer(axfer_txn_id, expected_asset) >= expected_amount, S("ERR:AXFER AMT")
    )


@subroutine
def payment(txn_idx: UInt64) -> UInt64:
    pay_txn = gtxn.PaymentTransaction(txn_idx)
    custom.ensure(
        pay_txn.receiver == Global.current_application_address,
        S("ERR:PAY RCV"),
    )
    return pay_txn.amount


@subroutine
def payment_amount_min(payment_txn_idx: UInt64, expected_amount: UInt64) -> None:
    custom.ensure(payment(payment_txn_idx) >= expected_amount, S("ERR:PAY AMT"))


@subroutine
def payment_amount_exact(payment_txn_idx: UInt64, expected_amount: UInt64) -> None:
    custom.ensure(payment(payment_txn_idx) == expected_amount, S("ERR:PAY AMT"))


# @subroutine
# def axfer_payment(
#     axfer_txn_idx: UInt64, expected_asset: Asset, expected_amount: UInt64
# ) -> None:
#     axfer_txn = gtxn.AssetTransferTransaction(axfer_txn_idx)
#     custom.ensure(axfer_txn.xfer_asset == expected_asset, S("ERR:AXFER ID"))
#     custom.ensure(axfer_txn.asset_amount == expected_amount, S("ERR:AXFER AMT"))
#     custom.ensure(
#         axfer_txn.asset_receiver == Global.current_application_address,
#         S("ERR:AXFER RCV"),
#     )
