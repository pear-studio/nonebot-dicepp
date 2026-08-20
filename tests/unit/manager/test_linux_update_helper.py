"""linux_update_helper 纯函数单元测试:_manager_create_config 配置推导。"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from dicepp_manager.docker_handoff import ContainerIdentity
from dicepp_manager.docker_runtime import DockerRuntimeError
from dicepp_manager import linux_update_helper

MANAGER_OLD_IMG = "sha256:" + ("d" * 64)
MANAGER_NEW_IMG = "sha256:" + ("e" * 64)


class TestManagerCreateConfig:
    """自切换创建请求不复制 daemon 维护的 MacAddress(endpoint 提取与 docker_upgrade 对齐)。"""

    def _identity(
        self, networks: Mapping[str, Any] | None = None
    ) -> ContainerIdentity:
        if networks is None:
            networks = {
                "dicepp_manager-net": {
                    "MacAddress": "7a:0c:3b:f5:1b:94",
                    "Gateway": "172.30.0.1",
                    "IPAddress": "172.30.0.2",
                    "NetworkID": "x" * 64,
                    "Aliases": ["dicepp-manager"],
                    "DriverOpts": None,
                    "IPAMConfig": None,
                }
            }
        return ContainerIdentity(
            container_id="c" * 64,
            name="dicepp-manager",
            image_id=MANAGER_OLD_IMG,
            image_reference="ghcr.io/pear-studio/dicepp-manager:test",
            running=True,
            restart_policy="unless-stopped",
            labels={"com.docker.compose.project": "dicepp"},
            config={
                "Image": "ghcr.io/pear-studio/dicepp-manager:test",
                "Hostname": "c" * 12,
                "MacAddress": "7a:0c:3b:f5:1b:94",
                "Cmd": ["python", "-m", "dicepp_manager"],
            },
            host_config={"Binds": ["/var/run/docker.sock:/var/run/docker.sock"]},
            networks=networks,
            hostname="c" * 12,
        )

    def test_drops_legacy_container_and_endpoint_mac_address(self) -> None:
        config = linux_update_helper._manager_create_config(
            self._identity(), MANAGER_NEW_IMG
        )

        # 容器级 legacy 字段不复制:omit 后由 daemon 自动分配新地址
        assert "MacAddress" not in config
        assert "Hostname" not in config
        assert config["Image"] == MANAGER_NEW_IMG
        endpoints = config["NetworkingConfig"]["EndpointsConfig"]
        assert set(endpoints) == {"dicepp_manager-net"}
        endpoint = endpoints["dicepp_manager-net"]
        # 与 docker_upgrade 路径一致:只保留连接参数,不复制 daemon 维护字段
        assert endpoint == {"Aliases": ["dicepp-manager"]}
        assert "MacAddress" not in endpoint
        assert "Gateway" not in endpoint
        assert "IPAddress" not in endpoint

    def test_malformed_endpoint_fails_closed(self) -> None:
        """endpoint 值非 dict 时与 docker_upgrade 路径同契约:DockerRuntimeError。"""
        identity = self._identity(networks={"dicepp_manager-net": None})

        with pytest.raises(DockerRuntimeError, match="network identity"):
            linux_update_helper._manager_create_config(
                identity, MANAGER_NEW_IMG
            )
