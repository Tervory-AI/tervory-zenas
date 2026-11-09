"""Run inside the existing, network-disabled pinned gateway CI container."""
from importlib.metadata import version
import os
from pathlib import Path
import sys

import yaml
from litellm.proxy.proxy_server import ProxyConfig


def main():
    assert version("litellm") == "1.106.4", "Contract requires the deployed gateway pin"
    # DECISION test the real proxy configuration in its existing gateway image,
    # without installing a provider SDK into product workers or skipping proof.
    os.environ["SARVAM_API_KEY"] = "placeholder-sarvam-header-contract"
    for filename in ("config.yaml", "config.za.yaml"):
        root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/app")
        config = yaml.safe_load((root / filename).read_text())
        assert all(row["model_name"] != "sarvam-105b-direct" for row in config["model_list"])
        held = next(row for row in config["x_unpriced_models"]
                    if row["model_name"] == "sarvam-105b-direct")
        # Resolve the retained, non-serving candidate only; never load a router
        # or make a provider request. This method is the installed proxy code.
        resolved = ProxyConfig()._check_for_os_environ_vars({"model_list": [held]})
        params = resolved["model_list"][0]["litellm_params"]
        # Ordinary api_key and nested headers both resolve during config
        # loading; only federation *_ref fields retain secret pointers.
        assert params["api_key"] == "placeholder-sarvam-header-contract"
        assert params["extra_headers"]["api-subscription-key"] == "placeholder-sarvam-header-contract"
    print("maker gateway contract: pinned proxy resolves both regional headers; unpriced routes held")


if __name__ == "__main__":
    main()
