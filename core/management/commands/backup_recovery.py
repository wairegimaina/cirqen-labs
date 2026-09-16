"""
Backup and Recovery System for Sync Service
- Automated backups of Redis state
- Database backup coordination
- Recovery procedures
- Disaster recovery automation
"""
import os
import json
import gzip
import shutil
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

import redis
import psycopg2

logger = logging.getLogger(__name__)


@dataclass
class BackupMetadata:
    """Backup metadata"""
    backup_id: str
    timestamp: datetime
    backup_type: str  # 'full', 'incremental', 'redis_only'
    size_bytes: int
    components: List[str]
    status: str  # 'completed', 'failed', 'partial'

    def to_dict(self):
        return {
            'backup_id': self.backup_id,
            'timestamp': self.timestamp.isoformat(),
            'backup_type': self.backup_type,
            'size_bytes': self.size_bytes,
            'components': self.components,
            'status': self.status
        }


class BackupManager:
    """
    Comprehensive backup and recovery manager
    """

    def __init__(self, backup_dir: str = None):
        self.backup_dir = Path(backup_dir or os.getenv('BACKUP_DIR', './backups'))
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self.redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            password=os.getenv('REDIS_PASSWORD'),
            decode_responses=True
        )

        # Retention policy
        self.retention_days = int(os.getenv('BACKUP_RETENTION_DAYS', 30))
        self.keep_minimum_backups = int(os.getenv('BACKUP_KEEP_MINIMUM', 5))

    def create_backup_id(self) -> str:
        """Generate unique backup ID"""
        return f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def backup_redis_streams(self, backup_id: str) -> Dict:
        """Backup Redis Streams data"""
        logger.info("📦 Backing up Redis Streams...")

        backup_path = self.backup_dir / backup_id / 'redis'
        backup_path.mkdir(parents=True, exist_ok=True)

        components_backed_up = []
        total_size = 0

        try:
            # Backup retry stream
            retry_messages = self._export_stream('sync:retry_stream')
            if retry_messages:
                file_path = backup_path / 'retry_stream.json.gz'
                size = self._write_compressed(file_path, retry_messages)
                total_size += size
                components_backed_up.append('retry_stream')
                logger.info(f"  ✓ Retry stream: {len(retry_messages)} messages ({size} bytes)")

            # Backup DLQ stream
            dlq_messages = self._export_stream('sync:dlq_stream')
            if dlq_messages:
                file_path = backup_path / 'dlq_stream.json.gz'
                size = self._write_compressed(file_path, dlq_messages)
                total_size += size
                components_backed_up.append('dlq_stream')
                logger.info(f"  ✓ DLQ stream: {len(dlq_messages)} messages ({size} bytes)")

            # Backup offsets
            offsets = self.redis_client.hgetall('sync:committed_offsets')
            if offsets:
                file_path = backup_path / 'offsets.json.gz'
                size = self._write_compressed(file_path, offsets)
                total_size += size
                components_backed_up.append('offsets')
                logger.info(f"  ✓ Offsets: {len(offsets)} entries ({size} bytes)")

            # Backup stats
            stats = self.redis_client.get('sync:stats')
            if stats:
                file_path = backup_path / 'stats.json.gz'
                size = self._write_compressed(file_path, {'stats': stats})
                total_size += size
                components_backed_up.append('stats')
                logger.info(f"  ✓ Stats backed up ({size} bytes)")

            # Backup alerts
            alerts = self.redis_client.lrange('sync:alerts', 0, -1)
            if alerts:
                file_path = backup_path / 'alerts.json.gz'
                size = self._write_compressed(file_path, alerts)
                total_size += size
                components_backed_up.append('alerts')
                logger.info(f"  ✓ Alerts: {len(alerts)} entries ({size} bytes)")

            logger.info(f"✅ Redis backup completed: {total_size} bytes")

            return {
                'status': 'success',
                'components': components_backed_up,
                'size_bytes': total_size
            }

        except Exception as e:
            logger.error(f"Redis backup failed: {e}", exc_info=True)
            return {
                'status': 'failed',
                'components': components_backed_up,
                'size_bytes': total_size,
                'error': str(e)
            }

    def _export_stream(self, stream_name: str) -> List[Dict]:
        """Export all messages from a Redis Stream"""
        try:
            messages = []
            start_id = '0-0'

            while True:
                results = self.redis_client.xrange(stream_name, min=start_id, count=1000)
                if not results:
                    break

                for msg_id, data in results:
                    messages.append({
                        'id': msg_id,
                        'data': data
                    })
                    start_id = f"({msg_id}"  # Next iteration starts after this ID

            return messages
        except Exception:
            return []

    def _write_compressed(self, file_path: Path, data: any) -> int:
        """Write compressed JSON data"""
        json_data = json.dumps(data).encode('utf-8')
        with gzip.open(file_path, 'wb') as f:
            f.write(json_data)
        return file_path.stat().st_size

    def backup_postgres(self, backup_id: str, db_alias: str) -> Dict:
        """Backup PostgreSQL database using pg_dump"""
        logger.info(f"📦 Backing up PostgreSQL {db_alias}...")

        backup_path = self.backup_dir / backup_id / 'postgres'
        backup_path.mkdir(parents=True, exist_ok=True)

        try:
            if db_alias == 'default':
                host = os.getenv('POSTGRES_LOCAL_HOST', '127.0.0.1')
                port = os.getenv('POSTGRES_LOCAL_PORT', '5455')
                database = os.getenv('POSTGRES_LOCAL_DB', 'localdb')
                user = os.getenv('POSTGRES_LOCAL_USER', 'localdb')
                password = os.getenv('POSTGRES_LOCAL_PASSWORD')
            else:
                host = os.getenv('POSTGRES_HQ_HOST', '127.0.0.1')
                port = os.getenv('POSTGRES_HQ_PORT', '5432')
                database = os.getenv('POSTGRES_HQ_DB', 'b12technologies')
                user = os.getenv('POSTGRES_HQ_USER', 'b12technologies')
                password = os.getenv('POSTGRES_HQ_PASSWORD')

            output_file = backup_path / f'{db_alias}.sql.gz'

            # Use pg_dump with compression
            env = os.environ.copy()
            env['PGPASSWORD'] = password

            cmd = [
                'pg_dump',
                '-h', host,
                '-p', str(port),
                '-U', user,
                '-d', database,
                '--format=custom',
                '--compress=9',
                '--file', str(output_file)
            ]

            result = subprocess.run(cmd, env=env, capture_output=True, text=True)

            if result.returncode == 0:
                size = output_file.stat().st_size
                logger.info(f"  ✓ PostgreSQL {db_alias}: {size} bytes")
                return {
                    'status': 'success',
                    'size_bytes': size
                }
            else:
                logger.error(f"pg_dump failed: {result.stderr}")
                return {
                    'status': 'failed',
                    'error': result.stderr
                }

        except Exception as e:
            logger.error(f"PostgreSQL backup failed: {e}", exc_info=True)
            return {
                'status': 'failed',
                'error': str(e)
            }

    def create_full_backup(self) -> BackupMetadata:
        """Create a full system backup"""
        backup_id = self.create_backup_id()
        logger.info(f"🔄 Starting full backup: {backup_id}")

        components_completed = []
        total_size = 0
        errors = []

        # Backup Redis
        redis_result = self.backup_redis_streams(backup_id)
        if redis_result['status'] == 'success':
            components_completed.extend(redis_result['components'])
            total_size += redis_result['size_bytes']
        else:
            errors.append(f"Redis: {redis_result.get('error')}")

        # Backup Local DB
        local_result = self.backup_postgres(backup_id, 'default')
        if local_result['status'] == 'success':
            components_completed.append('postgres_local')
            total_size += local_result['size_bytes']
        else:
            errors.append(f"Local DB: {local_result.get('error')}")

        # Backup HQ DB
        hq_result = self.backup_postgres(backup_id, 'hq')
        if hq_result['status'] == 'success':
            components_completed.append('postgres_hq')
            total_size += hq_result['size_bytes']
        else:
            errors.append(f"HQ DB: {hq_result.get('error')}")

        # Save metadata
        status = 'completed' if not errors else ('partial' if components_completed else 'failed')

        metadata = BackupMetadata(
            backup_id=backup_id,
            timestamp=datetime.now(),
            backup_type='full',
            size_bytes=total_size,
            components=components_completed,
            status=status
        )

        self._save_metadata(backup_id, metadata)

        if errors:
            logger.warning(f"⚠️ Backup completed with errors: {', '.join(errors)}")
        else:
            logger.info(f"✅ Full backup completed: {backup_id}")

        return metadata

    def _save_metadata(self, backup_id: str, metadata: BackupMetadata):
        """Save backup metadata"""
        metadata_file = self.backup_dir / backup_id / 'metadata.json'
        metadata_file.parent.mkdir(parents=True, exist_ok=True)

        with open(metadata_file, 'w') as f:
            json.dump(metadata.to_dict(), f, indent=2)

    def restore_redis_streams(self, backup_id: str) -> bool:
        """Restore Redis Streams from backup"""
        logger.info(f"🔄 Restoring Redis from backup: {backup_id}")

        backup_path = self.backup_dir / backup_id / 'redis'
        if not backup_path.exists():
            logger.error("Backup path not found")
            return False

        try:
            # Restore retry stream
            retry_file = backup_path / 'retry_stream.json.gz'
            if retry_file.exists():
                with gzip.open(retry_file, 'rt') as f:
                    messages = json.load(f)

                for msg in messages:
                    self.redis_client.xadd('sync:retry_stream', msg['data'], id=msg['id'])

                logger.info(f"  ✓ Restored {len(messages)} retry messages")

            # Restore DLQ stream
            dlq_file = backup_path / 'dlq_stream.json.gz'
            if dlq_file.exists():
                with gzip.open(dlq_file, 'rt') as f:
                    messages = json.load(f)

                for msg in messages:
                    self.redis_client.xadd('sync:dlq_stream', msg['data'], id=msg['id'])

                logger.info(f"  ✓ Restored {len(messages)} DLQ messages")

            # Restore offsets
            offsets_file = backup_path / 'offsets.json.gz'
            if offsets_file.exists():
                with gzip.open(offsets_file, 'rt') as f:
                    offsets = json.load(f)

                for key, value in offsets.items():
                    self.redis_client.hset('sync:committed_offsets', key, value)

                logger.info(f"  ✓ Restored {len(offsets)} offsets")

            logger.info("✅ Redis restore completed")
            return True

        except Exception as e:
            logger.error(f"Redis restore failed: {e}", exc_info=True)
            return False

    def restore_postgres(self, backup_id: str, db_alias: str) -> bool:
        """Restore PostgreSQL database from backup"""
        logger.info(f"🔄 Restoring PostgreSQL {db_alias} from backup: {backup_id}")

        backup_file = self.backup_dir / backup_id / 'postgres' / f'{db_alias}.sql.gz'
        if not backup_file.exists():
            logger.error(f"Backup file not found: {backup_file}")
            return False

        try:
            if db_alias == 'default':
                host = os.getenv('POSTGRES_LOCAL_HOST', '127.0.0.1')
                port = os.getenv('POSTGRES_LOCAL_PORT', '5455')
                database = os.getenv('POSTGRES_LOCAL_DB', 'localdb')
                user = os.getenv('POSTGRES_LOCAL_USER', 'localdb')
                password = os.getenv('POSTGRES_LOCAL_PASSWORD')
            else:
                host = os.getenv('POSTGRES_HQ_HOST', '127.0.0.1')
                port = os.getenv('POSTGRES_HQ_PORT', '5432')
                database = os.getenv('POSTGRES_HQ_DB', 'b12technologies')
                user = os.getenv('POSTGRES_HQ_USER', 'b12technologies')
                password = os.getenv('POSTGRES_HQ_PASSWORD')

            env = os.environ.copy()
            env['PGPASSWORD'] = password

            cmd = [
                'pg_restore',
                '-h', host,
                '-p', str(port),
                '-U', user,
                '-d', database,
                '--clean',
                '--if-exists',
                str(backup_file)
            ]

            result = subprocess.run(cmd, env=env, capture_output=True, text=True)

            if result.returncode == 0:
                logger.info(f"✅ PostgreSQL {db_alias} restored successfully")
                return True
            else:
                logger.error(f"pg_restore failed: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"PostgreSQL restore failed: {e}", exc_info=True)
            return False

    def list_backups(self) -> List[BackupMetadata]:
        """List all available backups"""
        backups = []

        for backup_dir in self.backup_dir.iterdir():
            if backup_dir.is_dir():
                metadata_file = backup_dir / 'metadata.json'
                if metadata_file.exists():
                    with open(metadata_file) as f:
                        data = json.load(f)
                        data['timestamp'] = datetime.fromisoformat(data['timestamp'])
                        backups.append(BackupMetadata(**data))

        return sorted(backups, key=lambda x: x.timestamp, reverse=True)

    def cleanup_old_backups(self):
        """Remove old backups according to retention policy"""
        logger.info("🧹 Cleaning up old backups...")

        backups = self.list_backups()
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)

        # Keep at least minimum number of backups
        backups_to_keep = backups[:self.keep_minimum_backups]

        deleted_count = 0
        for backup in backups[self.keep_minimum_backups:]:
            if backup.timestamp < cutoff_date:
                backup_path = self.backup_dir / backup.backup_id
                shutil.rmtree(backup_path)
                deleted_count += 1
                logger.info(f"  ✓ Deleted old backup: {backup.backup_id}")

        logger.info(f"✅ Cleanup completed: {deleted_count} backups removed")


# CLI Interface
if __name__ == '__main__':
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    manager = BackupManager()

    if len(sys.argv) < 2:
        print("""
B12 Backup & Recovery System

Usage:
  python backup_recovery.py backup          - Create full backup
  python backup_recovery.py list            - List all backups
  python backup_recovery.py restore <id>    - Restore from backup
  python backup_recovery.py cleanup         - Remove old backups
        """)
        sys.exit(1)

    command = sys.argv[1]

    if command == 'backup':
        metadata = manager.create_full_backup()
        print(f"\n✅ Backup created: {metadata.backup_id}")
        print(f"   Size: {metadata.size_bytes:,} bytes")
        print(f"   Components: {', '.join(metadata.components)}")

    elif command == 'list':
        backups = manager.list_backups()
        print(f"\n📋 Available backups ({len(backups)}):\n")
        for backup in backups:
            status_icon = '✅' if backup.status == 'completed' else '⚠️'
            print(f"{status_icon} {backup.backup_id}")
            print(f"   Date: {backup.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"   Type: {backup.backup_type}")
            print(f"   Size: {backup.size_bytes:,} bytes")
            print(f"   Components: {', '.join(backup.components)}")
            print()

    elif command == 'restore':
        if len(sys.argv) < 3:
            print("❌ Error: Backup ID required")
            sys.exit(1)

        backup_id = sys.argv[2]
        print(f"\n🔄 Restoring from backup: {backup_id}")
        print("⚠️  WARNING: This will overwrite existing data!")
        confirm = input("Continue? (yes/no): ")

        if confirm.lower() == 'yes':
            redis_ok = manager.restore_redis_streams(backup_id)
            if redis_ok:
                print("✅ Restoration completed")
            else:
                print("❌ Restoration failed")
        else:
            print("❌ Restoration cancelled")

    elif command == 'cleanup':
        manager.cleanup_old_backups()

    else:
        print(f"❌ Unknown command: {command}")
        sys.exit(1)
