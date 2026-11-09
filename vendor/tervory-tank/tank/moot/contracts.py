"""Media identities are derived from trusted hearing projections, never browser roles."""
from dataclasses import dataclass
import hashlib
from uuid import UUID

SEATS = {'applicant', 'respondent', 'judge'}


class MediaError(ValueError):
    pass


@dataclass(frozen=True)
class Scope:
    tenant: str
    region: str
    hearing: str

    def __post_init__(self):
        UUID(self.tenant)
        UUID(self.hearing)
        if self.region not in {'us-east', 'za'}:
            raise MediaError('wrong_region')

    @property
    def room(self):
        return f'moot:{self.region}:{self.tenant}:{self.hearing}'


def participant(scope, user, seat, epoch):
    digest = hashlib.sha256(user.encode()).hexdigest()[:32]
    return f'{seat}:{epoch}:{digest}'


def authorize_projection(scope, user, seat, projection):
    if seat not in SEATS or projection.get('id') != scope.hearing or projection.get('region') != scope.region:
        raise MediaError('not_found')
    controller = projection.get('seats', {}).get(seat, {})
    if seat not in projection.get('my_seats', []) or controller.get('kind') != 'human':
        raise MediaError('seat_not_owned')
    if projection.get('phase') not in {'locked', 'hearing'}:
        raise MediaError('hearing_not_active')
    epoch = controller.get('epoch')
    if type(epoch) is not int or epoch < 0:
        raise MediaError('invalid_epoch')
    return participant(scope, user, seat, epoch)
