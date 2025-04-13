from algopy import (
    String,
    log,
    op,
    subroutine,
)


@subroutine
def ensure(cond: bool, msg: String) -> None:  # noqa: FBT001
    if not cond:
        log(msg)
        op.err()
