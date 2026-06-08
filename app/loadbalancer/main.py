from itertools import cycle

from fastapi import FastAPI, Request
import httpx
import json


# Create a FastAPI app
app = FastAPI()


# Load backend servers from JSON file
with open("servers.json") as f:
    servers = json.load(f)


# Implement a round-robin load balancer
class LoadBalancer:
    def __init__(self, servers):
        self.servers = servers
        self.pool = cycle(server["url"] for server in servers)

    def round_robin(self):
        return next(self.pool)


load_balancer = LoadBalancer(servers)


@app.get("/{path:path}")
@app.post("/{path:path}")
@app.put("/{path:path}")
@app.delete("/{path:path}")
@app.patch("/{path:path}")
async def proxy(request: Request, path: str):
    backend_url = load_balancer.round_robin()
    url = f"{backend_url}/{path}"

    # Forward the request
    async with httpx.AsyncClient() as client:
        response = await client.request(
            request.method, url, headers=request.headers.raw, data=await request.body()
        )

    return response.json()