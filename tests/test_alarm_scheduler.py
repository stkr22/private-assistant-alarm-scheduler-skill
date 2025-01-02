import logging
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import jinja2
import respx
from httpx import Response
from private_assistant_commons import messages
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from private_assistant_alarm_scheduler_skill import models
from private_assistant_alarm_scheduler_skill.alarm_scheduler_skill import Action, AlarmSchedulerSkill, Parameters


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
        self.mock_config.cron_expression = "0 6 * * *"  # Daily at 6 AM for cron tests
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

    async def test_find_parameters(self):
        # Mock IntentAnalysisResult for different actions

        # SET Action
        mock_intent_result_set = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result_set.nouns = ["alarm"]
        mock_intent_result_set.numbers = [Mock(number_token=6, next_token="o'clock")]
        mock_intent_result_set.client_request = Mock()

        parameters_set = await self.skill.find_parameters(Action.SET, mock_intent_result_set)
        self.assertIsInstance(parameters_set, Parameters)
        self.assertIsNotNone(parameters_set.alarm_time)
        self.assertEqual(parameters_set.alarm_time.hour, 6)

        # GET_ACTIVE Action (No active alarm scenario)
        mock_intent_result_get_active = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result_get_active.client_request = Mock()

        parameters_get_active = await self.skill.find_parameters(Action.GET_ACTIVE, mock_intent_result_get_active)
        self.assertIsInstance(parameters_get_active, Parameters)
        self.assertIsNone(parameters_get_active.alarm_time)

        # CONTINUE Action (Should calculate next cron time)
        mock_intent_result_continue = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result_continue.client_request = Mock()

        parameters_continue = await self.skill.find_parameters(Action.CONTINUE, mock_intent_result_continue)
        self.assertIsInstance(parameters_continue, Parameters)
        self.assertIsNotNone(parameters_continue.alarm_time)
        self.assertGreater(parameters_continue.alarm_time, datetime.now())

        # SKIP Action (Should calculate the second next cron time)
        mock_intent_result_skip = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result_skip.client_request = Mock()

        parameters_skip = await self.skill.find_parameters(Action.SKIP, mock_intent_result_skip)
        self.assertIsInstance(parameters_skip, Parameters)
        self.assertIsNotNone(parameters_skip.alarm_time)
        self.assertGreater(parameters_skip.alarm_time, datetime.now())

    def test_calculate_next_cron_execution_no_skip(self):
        # Test calculate_next_cron_execution without skipping
        next_execution = self.skill.calculate_next_cron_execution(skip_next=False)
        self.assertGreater(next_execution, datetime.now())
        self.assertEqual(next_execution.hour, 6)

    def test_calculate_next_cron_execution_skip(self):
        # Test calculate_next_cron_execution with skipping the next occurrence
        first_execution = self.skill.calculate_next_cron_execution(skip_next=False)
        second_execution = self.skill.calculate_next_cron_execution(skip_next=True)
        self.assertGreater(second_execution, first_execution)
        self.assertEqual(second_execution.hour, 6)

    async def test_set_next_alarm_from_cron(self):
        # Mock register_alarm to verify it gets called with correct parameters
        with patch.object(self.skill, "register_alarm") as mock_register_alarm:
            await self.skill.set_next_alarm_from_cron()

            # Verify that register_alarm was called with the correct parameters
            mock_register_alarm.assert_called_once()
            parameters = mock_register_alarm.call_args[0][0]
            self.assertIsInstance(parameters.alarm_time, datetime)
            self.assertGreater(parameters.alarm_time, datetime.now())
            self.assertEqual(parameters.alarm_time.hour, 6)

    async def test_skip_alarm(self):
        # Mock register_alarm to verify it gets called with correct parameters after skipping
        with patch.object(self.skill, "register_alarm") as mock_register_alarm:
            await self.skill.skip_alarm()

            # Verify that register_alarm was called with the correct parameters
            mock_register_alarm.assert_called_once()
            parameters = mock_register_alarm.call_args[0][0]
            self.assertIsInstance(parameters.alarm_time, datetime)
            self.assertGreater(parameters.alarm_time, datetime.now())
            self.assertEqual(parameters.alarm_time.hour, 6)

    async def test_trigger_alarm_success(self):
        with respx.mock as respx_mock:
            # Set up the mocked API route for success
            respx_mock.post(self.mock_config.webhook_url).mock(return_value=Response(200, json={"message": "success"}))
            with patch.object(self.skill, "set_next_alarm_from_cron") as mock_set_next_alarm_from_cron:
                # Trigger alarm
                await self.skill.trigger_alarm()

                # Verify the call to set the next alarm
                mock_set_next_alarm_from_cron.assert_called_once()

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
                self.assertTrue("An error occurred while triggering alarm:" in self.mock_logger.error.call_args[0][0])

                # Verify the retry logic
                mock_set_next_alarm_from_cron.assert_called_once()

    async def test_break_execution(self):
        # Add a mock active alarm to the database
        mock_alarm = models.ASSActiveAlarm(
            name="Test Alarm",
            scheduled_time=datetime.now() + timedelta(hours=1),
        )
        async with AsyncSession(self.engine_async) as session, session.begin():
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
