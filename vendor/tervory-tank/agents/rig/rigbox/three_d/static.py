"""Public renderer modules; model bytes retain their authenticated API route."""
from fastapi.staticfiles import StaticFiles


class ViewerFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200 and path.endswith((".js", ".wasm")):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response
