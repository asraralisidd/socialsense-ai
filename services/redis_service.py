import json
import logging
import os
from typing import Any, Optional

import redis

logger = logging.getLogger(__name__)


class RedisService:
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
        self._client: Optional[redis.Redis] = None
        self._connected = False

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
            )
        return self._client

    def check_connection(self) -> bool:
        try:
            self.client.ping()
            self._connected = True
            return True
        except redis.RedisError as e:
            logger.error(f'Redis connection failed: {e}')
            self._connected = False
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get(self, key: str) -> Optional[str]:
        try:
            val = self.client.get(key)
            return val
        except redis.RedisError as e:
            logger.error(f'Redis GET {key} failed: {e}')
            return None

    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        try:
            self.client.set(key, value, ex=ex)
            return True
        except redis.RedisError as e:
            logger.error(f'Redis SET {key} failed: {e}')
            return False

    def delete(self, key: str) -> bool:
        try:
            self.client.delete(key)
            return True
        except redis.RedisError as e:
            logger.error(f'Redis DELETE {key} failed: {e}')
            return False

    def cache_get(self, key: str, namespace: str = 'cache') -> Optional[Any]:
        val = self.get(f'{namespace}:{key}')
        if val:
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return val
        return None

    def cache_set(self, key: str, value: Any, ttl: int = 300, namespace: str = 'cache') -> bool:
        try:
            data = json.dumps(value)
        except (TypeError, ValueError):
            data = str(value)
        return self.set(f'{namespace}:{key}', data, ex=ttl)

    def cache_delete(self, key: str, namespace: str = 'cache') -> bool:
        return self.delete(f'{namespace}:{key}')

    def rate_limit(self, key: str, max_attempts: int = 10, window: int = 60) -> bool:
        pipe = self.client.pipeline()
        now = 0
        try:
            now = self.client.time()[0]
        except Exception:
            import time
            now = int(time.time())
        window_start = now - window
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        results = pipe.execute()
        count = results[1] if results else 0
        if count >= max_attempts:
            return False
        # Unique member per attempt: identical timestamps within the same
        # second must not collapse into one sorted-set member, otherwise
        # bursts would never reach max_attempts.
        import uuid
        member = f'{now}:{uuid.uuid4().hex[:8]}'
        self.client.zadd(key, {member: now})
        self.client.expire(key, window)
        return True

    def get_queue_length(self, queue_name: str = 'celery') -> int:
        try:
            return self.client.llen(queue_name)
        except redis.RedisError as e:
            logger.error(f'Redis queue length failed: {e}')
            return 0

    def get_info(self) -> dict:
        try:
            info = self.client.info()
            return {
                'connected_clients': info.get('connected_clients', 0),
                'used_memory_human': info.get('used_memory_human', 'unknown'),
                'uptime_in_seconds': info.get('uptime_in_seconds', 0),
                'total_connections_received': info.get('total_connections_received', 0),
                'total_commands_processed': info.get('total_commands_processed', 0),
                'keyspace_hits': info.get('keyspace_hits', 0),
                'keyspace_misses': info.get('keyspace_misses', 0),
            }
        except redis.RedisError:
            return {}


redis_service = RedisService()
