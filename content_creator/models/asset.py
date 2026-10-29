from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class AssetEnv:
    region: str
    project_name: str
    ark_model_endpoint: str
    tos_region: str
    tos_bucket: str
    tos_endpoint: str
    tos_public_url_prefix: str
    tos_prefix: str


@dataclass
class SingleAssetResult:
    group_id: str
    asset_id: str
    asset_url: str


@dataclass
class MultiAssetResult:
    group_id: str
    asset_ids: Union[list[str], dict[str, str]]
    asset_urls: Union[list[str], dict[str, str]]
