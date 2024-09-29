import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import jinja2
import respx
from httpx import Response
from private_assistant_commons import ClientRequest, IntentAnalysisResult, NumberAnalysisResult, messages

from private_assistant_alarm_scheduler_skill import models
from private_assistant_alarm_scheduler_skill.alarm_scheduler_skill import (
    Action,
    AlarmSchedulerSkill,
    Parameters,
    logger,
)


class MockedAPIMixin:
    @classmethod
    def setUpClass(cls):
        # Set up the base mocked API
        cls.mocked_api = respx.mock(base_url="https://example.org/api", assert_all_called=False)

    def setUp(self):
        # Start the mocked API for each test case
        self.mocked_api.start()
        self.addCleanup(self.mocked_api.stop)


class TestAlarmScheduler(MockedAPIMixin, unittest.TestCase):
    def setUp(self):
        # Mock the MQTT client, config, and Jinja2 template environment
        super().setUp()
        self.mock_mqtt_client = Mock()
        self.mock_config = Mock()
        self.mock_config.webhook_url = "https://example.org/api"
        self.mock_template_env = jinja2.Environment(
            loader=jinja2.PackageLoader(
                "private_assistant_alarm_scheduler_skill",
                "templates",
            )
        )
        # Instantiate AlarmScheduler with the mocks
        self.skill = AlarmSchedulerSkill(
            config_obj=self.mock_config,
            mqtt_client=self.mock_mqtt_client,
            template_env=self.mock_template_env,
            db_engine=Mock(),
        )

    def test_register_alarm(self):
        # Mock the IntentAnalysisResult
        mock_client_request = Mock()
        mock_client_request.output_topic = "test_topic"

        parameters = Parameters(alarm_time=datetime(2023, 3, 15, 6, 30))
        active_alarm = models.ASSActiveAlarm(
            name=parameters.alarm_name,
            scheduled_time=parameters.alarm_time,
        )

        with patch("private_assistant_alarm_scheduler_skill.alarm_scheduler_skill.Session") as mock_session:
            mock_session_instance = mock_session.return_value.__enter__.return_value
            mock_session_instance.exec.return_value.all.return_value = []

            # Call the register_alarm method
            self.skill.register_alarm(parameters)

            # Verify that the alarm was added to the session and committed
            mock_session_instance.add.assert_called_with(active_alarm)
            mock_session_instance.commit.assert_called()

    def test_trigger_alarm_success(self):
        with patch.object(self.skill, "set_next_alarm_from_cron") as mock_set_next_alarm_from_cron:
            # Set up the mocked API route for success
            alarm_time = datetime(2023, 3, 15, 6, 30)
            self.mocked_api.post("/", name="trigger_alarm_success").return_value = Response(
                200, json={"message": "success"}
            )

            # Call the trigger_alarm method
            self.skill.trigger_alarm(alarm_time)

            # Verify the call
            self.assertTrue(self.mocked_api["trigger_alarm_success"].called)
            self.assertEqual(self.mocked_api.calls.call_count, 1)
            mock_set_next_alarm_from_cron.assert_called_once()

    def test_trigger_alarm_failure(self):
        with patch.object(self.skill, "set_next_alarm_from_cron") as mock_set_next_alarm_from_cron:
            # Set up the mocked API route for failure
            alarm_time = datetime(2023, 3, 15, 6, 30)
            self.mocked_api.post("/", name="trigger_alarm_failure").return_value = Response(
                500, json={"error": "internal server error"}
            )

            with self.assertLogs(logger, level="ERROR") as cm:
                # Call the trigger_alarm method
                self.skill.trigger_alarm(alarm_time)

                # Check that an error log is generated
                self.assertTrue(any("Failed to trigger alarm" in message for message in cm.output))

            # Verify the call
            self.assertTrue(self.mocked_api["trigger_alarm_failure"].called)
            self.assertEqual(self.mocked_api.calls.call_count, 1)
            mock_set_next_alarm_from_cron.assert_called_once()

    def test_break_execution(self):
        with patch("private_assistant_alarm_scheduler_skill.alarm_scheduler_skill.Session") as mock_session:
            mock_session_instance = mock_session.return_value.__enter__.return_value
            # Call the break_execution method
            self.skill.break_execution()

            mock_session_instance.commit.assert_called_once()

            # Ensure the timer is cancelled
            self.assertIsNone(self.skill.active_timer)

    def test_continue_execution(self):
        with patch.object(self.skill, "set_next_alarm_from_cron") as mock_set_next_alarm_from_cron:
            # Call the continue_execution method
            self.skill.continue_execution()

            # Verify the alarm schedule was resumed
            mock_set_next_alarm_from_cron.assert_called_once()

    def test_skip_alarm(self):
        with patch("private_assistant_alarm_scheduler_skill.alarm_scheduler_skill.Session") as mock_session:
            mock_session_instance = mock_session.return_value.__enter__.return_value

            # Mock the current datetime and cron expression
            cron_expression = "0 6 * * *"
            self.mock_config.cron_expression = cron_expression
            # Call the skip_alarm method
            self.skill.skip_alarm()

            # Verify that the session was used correctly
            mock_session_instance.add.assert_called_once()
            mock_session_instance.commit.assert_called_once()

    def test_set_next_alarm(self):
        # Mock datetime and threading.Timer
        mock_next_execution = datetime.now() + timedelta(hours=5)

        with patch("private_assistant_alarm_scheduler_skill.alarm_scheduler_skill.threading.Timer") as mock_timer:
            # Call the set_next_alarm method
            self.skill.set_next_alarm(mock_next_execution)

            # Verify that a new timer was started
            mock_timer.assert_called_once()
            # Check if the timer was started
            self.assertTrue(self.skill.active_timer.daemon)

    def test_set_next_alarm_from_cron(self):
        cron_expression = "0 6 * * *"  # Every day at 6:00 AM
        self.mock_config.cron_expression = cron_expression

        with (
            patch("private_assistant_alarm_scheduler_skill.alarm_scheduler_skill.Session") as mock_session,
            patch("private_assistant_alarm_scheduler_skill.alarm_scheduler_skill.threading.Timer") as mock_timer,
        ):
            mock_session_instance = mock_session.return_value.__enter__.return_value

            # Call the set_next_alarm_from_cron method
            self.skill.set_next_alarm_from_cron()

            # Verify that the next alarm was calculated and added to the session
            mock_session_instance.add.assert_called_once()
            mock_session_instance.commit.assert_called_once()

            # Verify that a new timer was started
            mock_timer.assert_called_once()

    def test_process_request_continue(self):
        # Mock the IntentAnalysisResult
        mock_client_request = Mock()
        mock_client_request.text = "Continue the alarm schedule"

        mock_intent_result = Mock(spec=messages.IntentAnalysisResult)
        mock_intent_result.client_request = mock_client_request

        # Set up mock template return value
        mock_template = Mock()
        mock_template.render.return_value = "Alarm schedule has been resumed."
        self.skill.action_to_answer[Action.CONTINUE] = mock_template

        with patch.object(self.skill, "set_next_alarm_from_cron") as mock_set_next_alarm_from_cron:
            # Call the process_request method
            self.skill.process_request(mock_intent_result)

            # Verify the alarm schedule was resumed
            mock_set_next_alarm_from_cron.assert_called_once()

    def test_process_request_set(self):
        # Mock the IntentAnalysisResult
        now_time = (datetime.now() + timedelta(hours=2)).hour
        mock_client_request = ClientRequest(
            id=uuid.uuid4(), text=f"set an alarm for {now_time} o'clock", output_topic="test_topic", room="livingroom"
        )

        mock_number_analysis_result = NumberAnalysisResult(number_token=now_time, next_token="o'clock")
        mock_intent_result = IntentAnalysisResult(
            client_request=mock_client_request, numbers=[mock_number_analysis_result], nouns=["alarm"], verbs=["set"]
        )

        parameters = Parameters(alarm_time=datetime.now().replace(hour=now_time, minute=0, second=0, microsecond=0))
        active_alarm = models.ASSActiveAlarm(
            name="User Alarm",
            scheduled_time=parameters.alarm_time,
        )

        with patch("private_assistant_alarm_scheduler_skill.alarm_scheduler_skill.Session") as mock_session:
            mock_session_instance = mock_session.return_value.__enter__.return_value
            self.skill.process_request(mock_intent_result)

            # Verify that the alarm was added to the session and committed
            mock_session_instance.add.assert_called_with(active_alarm)
            mock_session_instance.commit.assert_called()
