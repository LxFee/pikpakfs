import httpx, json
from enum import Enum

class Aria2Status(Enum):
    ACTIVE = "active"
    WAITING = "waiting"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETE = "complete"
    REMOVED = "removed" 

ARIA_ADDRESS = "http://100.96.0.2:6800/jsonrpc"
ARIA_SECRET = "jfaieofjosiefjoiaesjfoiasejf"
BASE_PATH = "/downloads"

client = httpx.AsyncClient()

async def addUri(uri, path):
    jsonreq = json.dumps({
        "jsonrpc" : "2.0",
        "id" : "pikpak",
        "method" : "aria2.addUri",
        "params" : [ f"token:{ARIA_SECRET}", [uri], 
            {
                "dir" : BASE_PATH,
                "out" : path
            }]
    })
    response = await client.post(ARIA_ADDRESS, data=jsonreq)
    result = json.loads(response.text)
    return result["result"]


async def tellStatus(gid) -> Aria2Status:
    jsonreq = json.dumps({
        "jsonrpc" : "2.0",
        "id" : "pikpak",
        "method" : "aria2.tellStatus",
        "params" : [ f"token:{ARIA_SECRET}", gid]
    })
    response = await client.post(ARIA_ADDRESS, data=jsonreq)
    result = json.loads(response.text)
    if "error" in result:
        return Aria2Status.REMOVED
    return Aria2Status(result["result"]["status"])

async def pause(gid):
    jsonreq = json.dumps({
        "jsonrpc" : "2.0",
        "id" : "pikpak",
        "method" : "aria2.pause",
        "params" : [ f"token:{ARIA_SECRET}", gid]
    })
    await client.post(ARIA_ADDRESS, data=jsonreq)

async def unpause(gid):
    jsonreq = json.dumps({
        "jsonrpc" : "2.0",
        "id" : "pikpak",
        "method" : "aria2.unpause",
        "params" : [ f"token:{ARIA_SECRET}", gid]
    })
    await client.post(ARIA_ADDRESS, data=jsonreq)

async def remove(gid):
    jsonreq = json.dumps({
        "jsonrpc" : "2.0",
        "id" : "pikpak",
        "method" : "aria2.remove",
        "params" : [ f"token:{ARIA_SECRET}", gid]
    })
    await client.post(ARIA_ADDRESS, data=jsonreq)