"""Explicit caller ownership history; absence is not evidence of historical ownership."""

from typing import Self

from pydantic import Field, model_validator

from .models import ContractModel, Identifier, Positive

MAX_OWNER_EPOCHS = 16


class ClientOwnerEpoch(ContractModel):
    owner_id: Identifier
    first_revision: Positive
    transfer_id: Identifier | None = None


class ClientOwnership(ContractModel):
    epochs: tuple[ClientOwnerEpoch, ...] = Field(min_length=1, max_length=MAX_OWNER_EPOCHS)
    recovery_transfer_id: Identifier | None = None

    @model_validator(mode="after")
    def ordered_history(self) -> Self:
        first = self.epochs[0]
        if first.first_revision != 1 or first.transfer_id is not None:
            raise ValueError("ownership must start at the original registration")
        if any(epoch.transfer_id is None for epoch in self.epochs[1:]):
            raise ValueError("ownership changes require original transfer IDs")
        if any(
            a.first_revision >= b.first_revision
            for a, b in zip(self.epochs, self.epochs[1:], strict=False)
        ):
            raise ValueError("owner revision intervals must be ordered")
        if len({epoch.owner_id for epoch in self.epochs}) != len(self.epochs) or len(
            {epoch.transfer_id for epoch in self.epochs}
        ) != len(self.epochs):
            raise ValueError("ownership or transfer IDs cannot be reused")
        if (
            self.recovery_transfer_id is not None
            and self.recovery_transfer_id != self.epochs[-1].transfer_id
        ):
            raise ValueError("recovery hold must belong to the latest transfer")
        return self

    def owner_at(self, revision: int) -> str | None:
        return next(
            (epoch.owner_id for epoch in reversed(self.epochs) if epoch.first_revision <= revision),
            None,
        )
