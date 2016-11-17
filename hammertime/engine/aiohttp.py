# hammertime: A high-volume http fetch library
# Copyright (C) 2016-  Delve Labs inc.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

import asyncio
from async_timeout import timeout

from aiohttp import ClientSession
from aiohttp.errors import ClientOSError
from ..http import StaticResponse
from ..ruleset import StopRequest, RejectRequest, IgnoreBody


class AioHttpEngine:

    def __init__(self, *, loop):
        self.loop = loop
        self.session = ClientSession(loop=loop)
        self.limiter = asyncio.Semaphore(50)

    async def perform(self, entry, heuristics):
        async with self.limiter:
            try:
                await heuristics.before_request(entry)

                return await self._perform(entry, heuristics)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                raise StopRequest("Timeout reached")
            except (ClientOSError):
                raise StopRequest("Host Unreachable")

    async def _perform(self, entry, heuristics):
        req = entry.request

        with timeout(0.5, loop=self.loop):
            response = await self.session.request(method=req.method, url=req.url, timeout=0.2)

        # When the request is simply rejected, we want to keep the persistent connection alive
        async with ProtectedSession(response, RejectRequest):
            resp = StaticResponse(response.status, response.headers)
            entry = entry._replace(response=resp)

            try:
                await heuristics.after_headers(entry)

                with timeout(2.0):
                    entry.response.content = await response.text()
                    entry.result.body = 'full'
            except IgnoreBody:
                entry.result.body = 'ignored'

        await heuristics.after_response(entry)

        return entry

    async def close(self):
        await self.session.close()


class ProtectedSession:
    """
    Make sure the parent context closes properly even if a certain type
    of exception is raised.
    """

    def __init__(self, context, exception_class):
        self.context = context
        self.exception_class = exception_class

    async def __aenter__(self):
        await self.context.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type == self.exception_class:
            # Simple release
            await self.context.__aexit__(None, None, None)
        else:
            # Close
            await self.context.__aexit__(exc_type, exc, tb)