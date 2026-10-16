"""Non-secret file settings and environment-only Graph credentials."""
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


class ConfigError(Exception):
    """Messages contain field names only, never submitted values."""


@dataclass(frozen=True)
class Room:
    id: str
    name: str
    upn: str


@dataclass(frozen=True)
class Settings:
    data_source: str = "dummy"
    room_name: str = "ミーティングルーム（表示PoC）"
    room_upn: str = ""
    rooms: tuple = ()

    def room_definitions(self):
        # Legacy single-room settings remain compatible with the default URL.
        return self.rooms or (Room("idoutai", self.room_name, self.room_upn),)


@dataclass(frozen=True, repr=False)
class Credentials:
    tenant_id: str = field(repr=False)
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)


def load_settings(path=CONFIG_PATH):
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ConfigError("config.tomlを読み込めません") from None
    if set(data) - {"DATA_SOURCE", "ROOM_NAME", "ROOM_UPN", "rooms"}:
        raise ConfigError("config.tomlに未対応の項目があります（認証情報は環境変数で指定）")
    source = data.get("DATA_SOURCE", "dummy")
    name = data.get("ROOM_NAME", "ミーティングルーム（表示PoC）")
    room = data.get("ROOM_UPN", "")
    if source not in ("dummy", "graph"):
        raise ConfigError("DATA_SOURCEはdummyまたはgraphを指定してください")
    if "rooms" in data:
        if "ROOM_NAME" in data or "ROOM_UPN" in data:
            raise ConfigError("roomsと旧ROOM_NAME/ROOM_UPNは併記できません")
        definitions = data["rooms"]
        if not isinstance(definitions, list) or not definitions:
            raise ConfigError("roomsを1室以上設定してください")
        rooms, ids, upns = [], set(), set()
        for definition in definitions:
            if not isinstance(definition, dict) or set(definition) != {"id", "name", "upn"}:
                raise ConfigError("各roomsにはid/name/upnだけを設定してください")
            identity, label, upn = (definition[key] for key in ("id", "name", "upn"))
            if not isinstance(identity, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identity):
                raise ConfigError("会議室idは小文字英数字とハイフンで指定してください")
            if identity in ids:
                raise ConfigError("会議室idが重複しています")
            if not isinstance(label, str) or not label.strip() or not isinstance(upn, str):
                raise ConfigError("会議室name/upnの形式を確認してください")
            if source == "graph" and (not upn.strip() or "@" not in upn or any(c.isspace() for c in upn)):
                raise ConfigError("graphモードには各会議室upnが必要です")
            if upn.strip() and upn.strip().lower() in upns:
                raise ConfigError("会議室upnが重複しています")
            ids.add(identity)
            upns.add(upn.strip().lower())
            rooms.append(Room(identity, label.strip(), upn.strip()))
        if "idoutai" not in ids:
            raise ConfigError("既存URL互換のためidoutaiを設定してください")
        return Settings(data_source=source, rooms=tuple(rooms))
    if not isinstance(name, str) or not name.strip() or not isinstance(room, str):
        raise ConfigError("ROOM_NAMEとROOM_UPNの設定形式を確認してください")
    if source == "graph" and (not room.strip() or "@" not in room or any(c.isspace() for c in room)):
        raise ConfigError("graphモードにはROOM_UPNの設定が必要です")
    return Settings(source, name.strip(), room.strip())


def load_credentials(environ=None):
    env = os.environ if environ is None else environ
    names = ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET")
    missing = [name for name in names if not env.get(name, "").strip()]
    if missing:
        raise ConfigError("環境変数が未設定です: " + ", ".join(missing))
    for name in names[:2]:
        try:
            UUID(env[name].strip())
        except ValueError:
            raise ConfigError(name + "はGUID形式で設定してください") from None
    return Credentials(env["TENANT_ID"].strip(), env["CLIENT_ID"].strip(), env["CLIENT_SECRET"])
