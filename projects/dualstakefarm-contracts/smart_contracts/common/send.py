from algopy import Account, Asset, Global, UInt64, itxn, subroutine


@subroutine
def optin(asset: Asset, fee: UInt64) -> None:
    axfer(asset, Global.current_application_address, UInt64(0), fee)


@subroutine
def send(asset_id: UInt64, receiver: Account, amount: UInt64, fee: UInt64) -> None:
    # Send algo or ASA payment
    if asset_id == 0:
        algo_pay(receiver, amount, fee)
    else:
        axfer(Asset(asset_id), receiver, amount, fee)


@subroutine
def axfer(asset: Asset, receiver: Account, amount: UInt64, fee: UInt64) -> None:
    itxn.AssetTransfer(
        xfer_asset=asset, asset_receiver=receiver, asset_amount=amount, fee=fee
    ).submit()
    return


@subroutine
def axfer_closeout(asset: Asset, receiver: Account, fee: UInt64) -> None:
    itxn.AssetTransfer(
        xfer_asset=asset,
        asset_receiver=receiver,
        asset_amount=0,
        asset_close_to=receiver,
        fee=fee,
    ).submit()
    return


@subroutine
def algo_pay(receiver: Account, amount: UInt64, fee: UInt64) -> None:
    itxn.Payment(receiver=receiver, amount=amount, fee=fee).submit()
    return


@subroutine
def algo_closeout(receiver: Account, fee: UInt64) -> None:
    itxn.Payment(
        receiver=receiver, amount=0, close_remainder_to=receiver, fee=fee
    ).submit()
    return
