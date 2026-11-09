"""Explicit temporary legacy bridge. No data migration, learning or bulk case copy."""
from .contracts import MediaError


class LegacyHearingBridge:
    def __init__(self, client, url, key, region):
        self.client, self.url, self.key, self.region = client, url.rstrip('/'), key, region

    async def projection(self, scope, user, seat=None, *, founder=False):
        if scope.region != self.region:
            raise MediaError('wrong_region')
        params = {'home_region': scope.region}
        if seat is not None:
            params['seat'] = seat
        try:
            response = await self.client.get(f'{self.url}/moot/hearings/{scope.hearing}',
                params=params, headers={'X-Tank-Key': self.key, 'X-Moot-Tenant': scope.tenant,
                'X-Moot-Actor': user, 'X-Moot-Founder': '1' if founder else '0'}, timeout=5)
            response.raise_for_status()
            result = response.json()
        except Exception:
            raise MediaError('hearing_unavailable') from None
        if result.get('id') != scope.hearing or result.get('region') != scope.region:
            raise MediaError('not_found')
        return result
