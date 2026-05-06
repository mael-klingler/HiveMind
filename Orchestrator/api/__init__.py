# Copyright 2025 Mael Klingler
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from .tickets import router as tickets_router
from .agents import router as agents_router
from .agent_profiles import router as agent_profiles_router
from .queue_api import router as queue_router
from .settings_api import router as settings_router
from .mcp_servers_api import router as mcp_servers_router
from .instructions_api import router as instructions_router
from .plugins_api import router as plugins_router
from .memory_api import router as memory_router
from .repos_api import router as repos_router
from .review_api import router as review_router
from .proxy import router as proxy_router
from .webhooks import router as webhooks_router


def register_routes(app):
    app.include_router(tickets_router)
    app.include_router(agents_router)
    app.include_router(agent_profiles_router)
    app.include_router(queue_router)
    app.include_router(settings_router)
    app.include_router(mcp_servers_router)
    app.include_router(instructions_router)
    app.include_router(plugins_router)
    app.include_router(memory_router)
    app.include_router(repos_router)
    app.include_router(review_router)
    app.include_router(proxy_router)
    app.include_router(webhooks_router)