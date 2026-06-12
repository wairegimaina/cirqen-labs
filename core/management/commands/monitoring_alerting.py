"""
Production Monitoring and Alerting System for Redpanda Sync
Provides health checks, metrics, and alerts
"""
import os
import time
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from threading import Thread, Event

import redis
import psycopg2
from kafka import KafkaAdminClient
from kafka.errors import KafkaError

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Health check result"""
    component: str
    status: str  # 'healthy', 'degraded', 'critical'
    message: str
    timestamp: datetime
    details: Dict = None

    def to_dict(self):
        return {
            **asdict(self),
            'timestamp': self.timestamp.isoformat()
        }


@dataclass
class Alert:
    """Alert definition"""
    severity: str  # 'info', 'warning', 'critical'
    component: str
    message: str
    timestamp: datetime
    details: Dict = None

    def to_dict(self):
        return {
            **asdict(self),
            'timestamp': self.timestamp.isoformat()
        }


class MonitoringSystem:
    """
    Comprehensive monitoring system for sync service
    - Health checks for all components
    - Metric collection and tracking
    - Alert generation and notification
    - Dashboard data export
    """

    def __init__(self, redis_client=None):
        self.redis_client = redis_client or self._init_redis()
        self.alert_history = []
        self.max_alert_history = 1000

        # Alert thresholds
        self.thresholds = {
            'failed_rate_percent': float(os.getenv('ALERT_FAILED_RATE', 10)),
            'queue_size_critical': int(os.getenv('ALERT_QUEUE_SIZE', 1000)),
            'dlq_size_warning': int(os.getenv('ALERT_DLQ_SIZE', 100)),
            'lag_seconds_warning': int(os.getenv('ALERT_LAG_SECONDS', 300)),
            'redis_stream_critical': int(os.getenv('ALERT_REDIS_STREAM', 5000)),
            'consecutive_failures': int(os.getenv('ALERT_CONSECUTIVE_FAILS', 50)),
        }

        # Email configuration
        self.email_enabled = os.getenv('ALERT_EMAIL_ENABLED', 'false').lower() == 'true'
        self.smtp_host = os.getenv('SMTP_HOST', 'localhost')
        self.smtp_port = int(os.getenv('SMTP_PORT', 587))
        self.smtp_user = os.getenv('SMTP_USER')
        self.smtp_password = os.getenv('SMTP_PASSWORD')
        self.alert_email = os.getenv('ALERT_EMAIL')

        # Slack configuration
        self.slack_enabled = os.getenv('ALERT_SLACK_ENABLED', 'false').lower() == 'true'
        self.slack_webhook = os.getenv('SLACK_WEBHOOK_URL')

    def _init_redis(self):
        """Initialize Redis connection for monitoring"""
        return redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            password=os.getenv('REDIS_PASSWORD'),
            decode_responses=True,
            socket_timeout=5
        )

    def check_postgres_health(self, alias: str) -> HealthStatus:
        """Check PostgreSQL database health"""
        try:
            if alias == 'default':
                host = os.getenv('POSTGRES_LOCAL_HOST', '127.0.0.1')
                port = int(os.getenv('POSTGRES_LOCAL_PORT', 5455))
                database = os.getenv('POSTGRES_LOCAL_DB', 'localdb')
                user = os.getenv('POSTGRES_LOCAL_USER', 'localdb')
                password = os.getenv('POSTGRES_LOCAL_PASSWORD')
            else:
                host = os.getenv('POSTGRES_HQ_HOST', '127.0.0.1')
                port = int(os.getenv('POSTGRES_HQ_PORT', 5432))
                database = os.getenv('POSTGRES_HQ_DB', 'b12technologies')
                user = os.getenv('POSTGRES_HQ_USER', 'b12technologies')
                password = os.getenv('POSTGRES_HQ_PASSWORD')

            start_time = time.time()
            conn = psycopg2.connect(
                host=host, port=port, database=database,
                user=user, password=password, connect_timeout=5
            )

            cursor = conn.cursor()

            # Check connections
            cursor.execute("SELECT count(*) FROM pg_stat_activity;")
            connection_count = cursor.fetchone()[0]

            # Check replication slots
            cursor.execute("""
                SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) as lag
                FROM pg_replication_slots
                WHERE slot_type = 'logical';
            """)
            replication_slots = cursor.fetchall()

            cursor.close()
            conn.close()

            latency_ms = (time.time() - start_time) * 1000

            return HealthStatus(
                component=f'postgres_{alias}',
                status='healthy',
                message=f'PostgreSQL {alias} is healthy',
                timestamp=datetime.now(),
                details={
                    'latency_ms': round(latency_ms, 2),
                    'connections': connection_count,
                    'replication_slots': len(replication_slots),
                    'host': host,
                    'port': port
                }
            )

        except psycopg2.OperationalError as e:
            return HealthStatus(
                component=f'postgres_{alias}',
                status='critical',
                message=f'Cannot connect to PostgreSQL {alias}',
                timestamp=datetime.now(),
                details={'error': str(e)}
            )
        except Exception as e:
            return HealthStatus(
                component=f'postgres_{alias}',
                status='degraded',
                message=f'PostgreSQL {alias} check failed',
                timestamp=datetime.now(),
                details={'error': str(e)}
            )

    def check_redis_health(self) -> HealthStatus:
        """Check Redis health"""
        try:
            start_time = time.time()
            self.redis_client.ping()
            latency_ms = (time.time() - start_time) * 1000

            # Get stream info
            try:
                retry_len = self.redis_client.xlen('sync:retry_stream')
                dlq_len = self.redis_client.xlen('sync:dlq_stream')
            except:
                retry_len = 0
                dlq_len = 0

            # Get memory info
            info = self.redis_client.info('memory')
            used_memory_mb = info['used_memory'] / (1024 * 1024)

            status = 'healthy'
            if retry_len > self.thresholds['redis_stream_critical']:
                status = 'degraded'

            return HealthStatus(
                component='redis',
                status=status,
                message='Redis is healthy',
                timestamp=datetime.now(),
                details={
                    'latency_ms': round(latency_ms, 2),
                    'retry_stream_length': retry_len,
                    'dlq_stream_length': dlq_len,
                    'memory_used_mb': round(used_memory_mb, 2)
                }
            )

        except redis.RedisError as e:
            return HealthStatus(
                component='redis',
                status='critical',
                message='Redis connection failed',
                timestamp=datetime.now(),
                details={'error': str(e)}
            )

    def check_kafka_health(self) -> HealthStatus:
        """Check Kafka/Redpanda health"""
        try:
            bootstrap_servers = os.getenv('REDPANDA_BOOTSTRAP_SERVERS', '127.0.0.1:9092').split(',')

            start_time = time.time()
            admin = KafkaAdminClient(
                bootstrap_servers=bootstrap_servers,
                request_timeout_ms=5000
            )

            # Get cluster metadata
            cluster_metadata = admin.list_topics()
            topic_count = len(cluster_metadata)

            admin.close()
            latency_ms = (time.time() - start_time) * 1000

            return HealthStatus(
                component='kafka',
                status='healthy',
                message='Kafka is healthy',
                timestamp=datetime.now(),
                details={
                    'latency_ms': round(latency_ms, 2),
                    'topic_count': topic_count,
                    'bootstrap_servers': bootstrap_servers
                }
            )

        except KafkaError as e:
            return HealthStatus(
                component='kafka',
                status='critical',
                message='Kafka connection failed',
                timestamp=datetime.now(),
                details={'error': str(e)}
            )

    def check_sync_service_health(self) -> HealthStatus:
        """Check sync service health from Redis state"""
        try:
            # Get latest stats from Redis
            stats_data = self.redis_client.get('sync:stats')
            if not stats_data:
                return HealthStatus(
                    component='sync_service',
                    status='degraded',
                    message='No recent stats available',
                    timestamp=datetime.now(),
                    details={'error': 'Stats not found in Redis'}
                )

            stats = json.loads(stats_data)

            # Calculate health metrics
            processed = stats.get('processed', 0)
            success = stats.get('success', 0)
            failed = stats.get('failed', 0)

            if processed > 0:
                success_rate = (success / processed) * 100
                failed_rate = (failed / processed) * 100
            else:
                success_rate = 100
                failed_rate = 0

            # Determine status
            status = 'healthy'
            message = 'Sync service is healthy'

            if failed_rate > self.thresholds['failed_rate_percent']:
                status = 'degraded'
                message = f'High failure rate: {failed_rate:.1f}%'

            dlq_count = stats.get('by_direction', {}).get('LOCAL_TO_HQ', {}).get('fk_failures', 0)
            if dlq_count > self.thresholds['dlq_size_warning']:
                status = 'degraded'
                message = f'High DLQ count: {dlq_count}'

            return HealthStatus(
                component='sync_service',
                status=status,
                message=message,
                timestamp=datetime.now(),
                details={
                    'processed': processed,
                    'success': success,
                    'failed': failed,
                    'success_rate': round(success_rate, 2),
                    'failed_rate': round(failed_rate, 2),
                    'redis_persisted': stats.get('redis_persisted', 0),
                    'redis_recovered': stats.get('redis_recovered', 0)
                }
            )

        except Exception as e:
            return HealthStatus(
                component='sync_service',
                status='critical',
                message='Failed to check sync service health',
                timestamp=datetime.now(),
                details={'error': str(e)}
            )

    def run_all_health_checks(self) -> List[HealthStatus]:
        """Run all health checks"""
        checks = [
            self.check_postgres_health('default'),
            self.check_postgres_health('hq'),
            self.check_redis_health(),
            self.check_kafka_health(),
            self.check_sync_service_health()
        ]
        return checks

    def generate_alerts(self, health_checks: List[HealthStatus]) -> List[Alert]:
        """Generate alerts based on health check results"""
        alerts = []

        for check in health_checks:
            if check.status == 'critical':
                alerts.append(Alert(
                    severity='critical',
                    component=check.component,
                    message=check.message,
                    timestamp=check.timestamp,
                    details=check.details
                ))
            elif check.status == 'degraded':
                alerts.append(Alert(
                    severity='warning',
                    component=check.component,
                    message=check.message,
                    timestamp=check.timestamp,
                    details=check.details
                ))

        return alerts

    def send_email_alert(self, alert: Alert):
        """Send email alert"""
        if not self.email_enabled or not self.alert_email:
            return

        try:
            msg = MIMEMultipart()
            msg['From'] = self.smtp_user
            msg['To'] = self.alert_email
            msg['Subject'] = f'[{alert.severity.upper()}] B12 Sync Alert: {alert.component}'

            body = f"""
B12 Technologies Sync System Alert

Severity: {alert.severity.upper()}
Component: {alert.component}
Time: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}

Message: {alert.message}

Details:
{json.dumps(alert.details, indent=2) if alert.details else 'No additional details'}

---
This is an automated alert from the B12 sync monitoring system.
            """

            msg.attach(MIMEText(body, 'plain'))

            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
            if self.smtp_user and self.smtp_password:
                server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
            server.quit()

            logger.info(f"Email alert sent for {alert.component}")

        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")

    def send_slack_alert(self, alert: Alert):
        """Send Slack alert"""
        if not self.slack_enabled or not self.slack_webhook:
            return

        try:
            import requests

            color_map = {
                'critical': '#FF0000',
                'warning': '#FFA500',
                'info': '#00FF00'
            }

            payload = {
                "attachments": [{
                    "color": color_map.get(alert.severity, '#808080'),
                    "title": f"{alert.severity.upper()}: {alert.component}",
                    "text": alert.message,
                    "fields": [
                        {
                            "title": "Timestamp",
                            "value": alert.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                            "short": True
                        }
                    ],
                    "footer": "B12 Sync Monitoring"
                }]
            }

            if alert.details:
                payload["attachments"][0]["fields"].append({
                    "title": "Details",
                    "value": json.dumps(alert.details, indent=2),
                    "short": False
                })

            response = requests.post(self.slack_webhook, json=payload, timeout=5)
            response.raise_for_status()

            logger.info(f"Slack alert sent for {alert.component}")

        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")

    def process_alerts(self, alerts: List[Alert]):
        """Process and send alerts"""
        for alert in alerts:
            # Add to history
            self.alert_history.append(alert)
            if len(self.alert_history) > self.max_alert_history:
                self.alert_history.pop(0)

            # Send notifications
            if alert.severity == 'critical':
                self.send_email_alert(alert)
                self.send_slack_alert(alert)
            elif alert.severity == 'warning':
                self.send_slack_alert(alert)

            # Store in Redis
            try:
                self.redis_client.lpush(
                    'sync:alerts',
                    json.dumps(alert.to_dict())
                )
                self.redis_client.ltrim('sync:alerts', 0, 999)  # Keep last 1000
            except:
                pass

    def export_metrics(self) -> Dict:
        """Export metrics in Prometheus format"""
        try:
            stats_data = self.redis_client.get('sync:stats')
            if stats_data:
                stats = json.loads(stats_data)
                return {
                    'sync_messages_processed_total': stats.get('processed', 0),
                    'sync_messages_success_total': stats.get('success', 0),
                    'sync_messages_failed_total': stats.get('failed', 0),
                    'sync_messages_skipped_total': stats.get('skipped', 0),
                    'sync_messages_retried_total': stats.get('retried', 0),
                    'sync_fk_failures_total': stats.get('fk_failures', 0),
                    'sync_redis_persisted_total': stats.get('redis_persisted', 0),
                    'sync_redis_recovered_total': stats.get('redis_recovered', 0),
                    'sync_runtime_seconds': stats.get('runtime', 0)
                }
            return {}
        except:
            return {}


class MonitoringThread(Thread):
    """Background monitoring thread"""

    def __init__(self, check_interval: int = 60):
        super().__init__(daemon=True)
        self.check_interval = check_interval
        self.stop_event = Event()
        self.monitor = MonitoringSystem()

    def run(self):
        logger.info("🔍 Monitoring thread started")

        while not self.stop_event.is_set():
            try:
                # Run health checks
                health_checks = self.monitor.run_all_health_checks()

                # Generate alerts
                alerts = self.monitor.generate_alerts(health_checks)

                # Process alerts
                if alerts:
                    self.monitor.process_alerts(alerts)

                # Log summary
                critical_count = sum(1 for c in health_checks if c.status == 'critical')
                degraded_count = sum(1 for c in health_checks if c.status == 'degraded')

                if critical_count > 0:
                    logger.error(f"❌ {critical_count} critical issues detected")
                elif degraded_count > 0:
                    logger.warning(f"⚠️ {degraded_count} degraded components")
                else:
                    logger.info("✅ All systems healthy")

                # Wait for next check
                self.stop_event.wait(self.check_interval)

            except Exception as e:
                logger.error(f"Monitoring error: {e}", exc_info=True)
                self.stop_event.wait(self.check_interval)

        logger.info("🔍 Monitoring thread stopped")

    def stop(self):
        """Stop monitoring"""
        self.stop_event.set()
