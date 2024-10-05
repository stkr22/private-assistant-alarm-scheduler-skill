import logging
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import jinja2
import respx
from httpx import Response
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from private_assistant_alarm_scheduler_skill import models
from private_assistant_alarm_scheduler_skill.alarm_scheduler_skill import AlarmSchedulerSkill, Parameters


class TestAlarmSchedulerSkill(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Set up an in-memory SQLite database for async usage
        cls.engine_async = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def asyncSetUp(self):
        # Create tables asynchronously before each test
        async with self.engine_async.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # Create mock components for testing
        self.mock_mqtt_client = AsyncMock()
        self.mock_config = Mock()
        self.mock_config.webhook_url = "https://example.org/api"
        self.mock_template_env = Mock(spec=jinja2.Environment)
        self.mock_task_group = AsyncMock()
        self.mock_logger = Mock(logging.Logger)

        # Create an instance of AlarmSchedulerSkill using the in-memory DB and mocked dependencies
        self.skill = AlarmSchedulerSkill(
            config_obj=self.mock_config,
            mqtt_client=self.mock_mqtt_client,
            db_engine=self.engine_async,
            template_env=self.mock_template_env,
            task_group=self.mock_task_group,
            logger=self.mock_logger,
        )

    async def asyncTearDown(self):
        # Drop tables asynchronously after each test to ensure a clean state
        async with self.engine_async.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)

    async def test_register_alarm(self):
        parameters = Parameters(alarm_time=datetime.now() + timedelta(minutes=10))
        with patch("private_assistant_alarm_scheduler_skill.alarm_scheduler_skill.AsyncSession") as mock_session:
            mock_session_instance = mock_session.return_value.__aenter__.return_value
            await self.skill.register_alarm(parameters)

            # Verify that the alarm was added to the session and committed
            mock_session_instance.add.assert_called_once()
            mock_session_instance.commit.assert_called_once()

    async def test_trigger_alarm_success(self):
        with respx.mock as respx_mock:
            # Set up the mocked API route for success
            respx_mock.post(self.mock_config.webhook_url).mock(return_value=Response(200, json={"message": "success"}))
            with patch.object(self.skill, "set_next_alarm_from_cron") as mock_set_next_alarm_from_cron:
                # Trigger alarm
                await self.skill.trigger_alarm()

                # Verify the call
                mock_set_next_alarm_from_cron.assert_called_once()

    async def test_break_execution(self):
        # Add a mock active alarm to the database
        mock_alarm = models.ASSActiveAlarm(
            name="Test Alarm",
            scheduled_time=datetime.now() + timedelta(hours=1),
        )
        async with AsyncSession(self.engine_async) as session:
            async with session.begin():
                session.add(mock_alarm)

        # Set a mock active timer task
        self.skill._active_alarm_task = AsyncMock()

        # Execute break_execution
        await self.skill.break_execution()

        # Verify that the active timer task was cancelled
        assert self.skill._active_alarm_task is None

        # Verify that the alarm was deleted from the database
        async with AsyncSession(self.engine_async) as session:
            result = await session.exec(select(models.ASSActiveAlarm))
            remaining_alarms = result.all()
            self.assertEqual(len(remaining_alarms), 0)

    async def test_skip_alarm(self):
        # Mock the current datetime and cron expression
        cron_expression = "0 6 * * *"  # Daily at 6:00 AM
        self.mock_config.cron_expression = cron_expression

        with patch.object(self.skill, "register_alarm") as mock_register_alarm:
            # Execute skip_alarm
            await self.skill.skip_alarm()

            # Verify that register_alarm was called
            mock_register_alarm.assert_called_once()

            # Check that the scheduled time in the parameters is later than the current time
            parameters = mock_register_alarm.call_args[0][0]
            self.assertIsInstance(parameters.alarm_time, datetime)
            self.assertGreater(parameters.alarm_time, datetime.now())

    async def test_trigger_alarm_failure(self):
        with respx.mock as respx_mock:
            # Set up the mocked API route for failure
            respx_mock.post(self.mock_config.webhook_url).mock(
                return_value=Response(500, json={"error": "internal server error"})
            )

            with patch.object(self.skill, "set_next_alarm_from_cron") as mock_set_next_alarm_from_cron:
                # Trigger alarm
                await self.skill.trigger_alarm()

                # Verify that an error log is generated
                self.mock_logger.error.assert_called_once()
                self.assertTrue("Failed to trigger alarm" in self.mock_logger.error.call_args[0][0])

                # Verify the retry logic
                mock_set_next_alarm_from_cron.assert_called_once()
