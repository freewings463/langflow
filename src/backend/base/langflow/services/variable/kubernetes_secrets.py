"""
模块名称：Kubernetes Secret 管理工具

本模块提供对 Kubernetes Secrets 的基本操作，包括创建、读取、更新和删除。
主要功能包括：
- 创建、读取、更新和删除 Kubernetes Secrets
- 提供用户ID编码功能以满足 Kubernetes 资源命名要求
- 处理 Secret 数据的 base64 编码和解码

关键组件：
- `KubernetesSecretManager`
- `encode_user_id`

设计背景：为变量服务提供 Kubernetes Secret 存储后端支持。
注意事项：需要集群访问权限，依赖 Kubernetes API。
"""

from base64 import b64decode, b64encode
from http import HTTPStatus
from uuid import UUID

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from lfx.log.logger import logger


class KubernetesSecretManager:
    """Kubernetes Secret 管理器。

    契约：提供对 Kubernetes Secrets 的创建、读取、更新和删除操作。
    副作用：与 Kubernetes API 交互。
    失败语义：Kubernetes API 调用失败时抛出 ApiException。
    """

    def __init__(self, namespace: str = "langflow"):
        """初始化 Kubernetes Secret 管理器。

        契约：加载 Kubernetes 配置并初始化 API 客户端。
        副作用：连接到 Kubernetes 集群。
        失败语义：Kubernetes 配置无效时抛出异常。
        """
        config.load_kube_config()
        self.namespace = namespace

        # initialize the Kubernetes API client
        self.core_api = client.CoreV1Api()

    def create_secret(
        self,
        name: str,
        data: dict,
        secret_type: str = "Opaque",  # noqa: S107
    ):
        """在指定命名空间中创建新 Secret。

        契约：创建具有指定名称和数据的 Kubernetes Secret。
        副作用：在 Kubernetes 集群中创建新的 Secret 资源。
        失败语义：创建失败时抛出 ApiException。
        """
        encoded_data = {k: b64encode(v.encode()).decode() for k, v in data.items()}

        secret_metadata = client.V1ObjectMeta(name=name)
        secret = client.V1Secret(
            api_version="v1", kind="Secret", metadata=secret_metadata, type=secret_type, data=encoded_data
        )

        return self.core_api.create_namespaced_secret(self.namespace, secret)

    def upsert_secret(self, secret_name: str, data: dict):
        """在指定命名空间中插入或更新 Secret。

        契约：如果 Secret 不存在则创建，如果存在则更新。
        副作用：在 Kubernetes 集群中创建或更新 Secret 资源。
        失败语义：操作失败时抛出 ApiException。
        """
        try:
            # Try to read the existing secret
            existing_secret = self.core_api.read_namespaced_secret(secret_name, self.namespace)

            # If secret exists, update it
            existing_data = {k: b64decode(v).decode() for k, v in existing_secret.data.items()}
            existing_data.update(data)

            # Encode all data to base64
            encoded_data = {k: b64encode(v.encode()).decode() for k, v in existing_data.items()}

            # Update the existing secret
            existing_secret.data = encoded_data
            return self.core_api.replace_namespaced_secret(secret_name, self.namespace, existing_secret)

        except ApiException as e:
            if e.status == HTTPStatus.NOT_FOUND:
                # Secret doesn't exist, create a new one
                return self.create_secret(secret_name, data)
            logger.exception(f"Error upserting secret {secret_name}")
            raise

    def get_secret(self, name: str) -> dict | None:
        """从指定命名空间读取 Secret。

        契约：获取指定名称的 Secret 数据。
        副作用：无。
        失败语义：如果 Secret 不存在则返回 None，其他错误抛出 ApiException。
        """
        try:
            secret = self.core_api.read_namespaced_secret(name, self.namespace)
            return {k: b64decode(v).decode() for k, v in secret.data.items()}
        except ApiException as e:
            if e.status == HTTPStatus.NOT_FOUND:
                return None
            raise

    def update_secret(self, name: str, data: dict):
        """更新指定命名空间中的现有 Secret。

        契约：更新指定名称的 Secret 数据。
        副作用：修改 Kubernetes 中的 Secret 资源。
        失败语义：Secret 不存在时抛出 ApiException。
        """
        # Get the existing secret
        secret = self.core_api.read_namespaced_secret(name, self.namespace)
        if secret is None:
            raise ApiException(status=404, reason="Not Found", msg="Secret not found")

        # Update the secret data
        encoded_data = {k: b64encode(v.encode()).decode() for k, v in data.items()}
        secret.data.update(encoded_data)

        # Update the secret in Kubernetes
        return self.core_api.replace_namespaced_secret(name, self.namespace, secret)

    def delete_secret_key(self, name: str, key: str):
        """从指定 Secret 中删除一个键。

        契约：从指定名称的 Secret 中删除指定键。
        副作用：修改 Kubernetes 中的 Secret 资源。
        失败语义：Secret 或键不存在时抛出 ApiException。
        """
        # Get the existing secret
        secret = self.core_api.read_namespaced_secret(name, self.namespace)
        if secret is None:
            raise ApiException(status=404, reason="Not Found", msg="Secret not found")

        # Delete the key from the secret data
        if key in secret.data:
            del secret.data[key]
        else:
            raise ApiException(status=404, reason="Not Found", msg="Key not found in the secret")

        # Update the secret in Kubernetes
        return self.core_api.replace_namespaced_secret(name, self.namespace, secret)

    def delete_secret(self, name: str):
        """从指定命名空间删除 Secret。

        契约：删除指定名称的 Secret。
        副作用：从 Kubernetes 中删除 Secret 资源。
        失败语义：删除失败时抛出 ApiException。
        """
        return self.core_api.delete_namespaced_secret(name, self.namespace)


# 决策：使用特殊编码函数处理用户ID以适应Kubernetes命名规范
# 问题：Kubernetes资源名称有字符限制，不能包含某些字符
# 方案：将用户ID转换为符合Kubernetes命名规则的格式
# 代价：需要额外的编码步骤
# 重评：当Kubernetes命名规则改变时

def encode_user_id(user_id: UUID | str) -> str:
    """将用户ID编码为符合Kubernetes Secret命名规范的格式。

    契约：将UUID或字符串用户ID转换为有效的Kubernetes资源名称。
    副作用：无。
    失败语义：无效的用户ID会抛出 ValueError。
    """
    # Handle UUID
    if isinstance(user_id, UUID):
        return f"uuid-{str(user_id).lower()}"[:253]

    # Convert string to lowercase
    user_id_ = str(user_id).lower()

    # If the user_id looks like an email, replace @ and . with allowed characters
    if "@" in user_id_ or "." in user_id_:
        user_id_ = user_id_.replace("@", "-at-").replace(".", "-dot-")

    # Encode the user_id to base64
    # encoded = base64.b64encode(user_id.encode("utf-8")).decode("utf-8")

    # Replace characters not allowed in Kubernetes names
    user_id_ = user_id_.replace("+", "-").replace("/", "_").rstrip("=")

    # Ensure the name starts with an alphanumeric character
    if not user_id_[0].isalnum():
        user_id_ = "a-" + user_id_

    # Truncate to 253 characters (Kubernetes name length limit)
    user_id_ = user_id_[:253]

    if not all(c.isalnum() or c in "-_" for c in user_id_):
        msg = f"Invalid user_id: {user_id_}"
        raise ValueError(msg)

    # Ensure the name ends with an alphanumeric character
    while not user_id_[-1].isalnum():
        user_id_ = user_id_[:-1]

    return user_id_
