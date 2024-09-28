import enum
import string
import threading
from datetime import datetime
from typing import Self

import httpx
import jinja2
import paho.mqtt.client as mqtt
import sqlalchemy
from croniter import croniter
from private_assistant_commons import BaseSkill, messages
from private_assistant_commons.skill_logger import SkillLogger
from pydantic import BaseModel
from sqlmodel import Session, select

from private_assistant_alarm_scheduler_skill import config, models

# Configure logging
logger = SkillLogger.get_logger(__name__)


class Parameters(BaseModel):
    alarm_time: datetime | None = None


class Action(enum.Enum):
    HELP = ["help"]
    SET = ["set"]
    SKIP = ["skip"]
    BREAK = ["break"]
    CONTINUE = ["continue"]
    GET_ACTIVE = ["current"]

    @classmethod
    def find_matching_action(cls, text: str) -> Self | None:
        text = text.translate(str.maketrans("", "", string.punctuation))
        text_words = set(text.lower().split())

        for action in cls:
            if all(word in text_words for word in action.value):
                return action
        return None


class AlarmSchedulerSkill(BaseSkill):
    def __init__(
        self,
        config_obj: config.SkillConfig,
        mqtt_client: mqtt.Client,
        db_engine: sqlalchemy.Engine,
        template_env: jinja2.Environment,
    ) -> None:
        super().__init__(config_obj, mqtt_client)
        self.mqtt_client: mqtt.Client = mqtt_client
        self.custom_skill_config = config_obj
        self.active_timer: threading.Timer | None = None
        self.db_engine: sqlalchemy.Engine = db_engine
        self.action_to_answer: dict[Action, jinja2.Template] = {
            Action.HELP: template_env.get_template("help.j2"),
            Action.SET: template_env.get_template("set.j2"),
            Action.GET_ACTIVE: template_env.get_template("get_active.j2"),
            Action.SKIP: template_env.get_template("skip.j2"),
            Action.BREAK: template_env.get_template("break.j2"),
            Action.CONTINUE: template_env.get_template("continue.j2"),
        }
        self.template_env: jinja2.Environment = template_env
        self.timer_lock: threading.RLock = threading.RLock()

    def calculate_certainty(self, intent_analysis_result: messages.IntentAnalysisResult) -> float:
        """Calculate how confident the skill is about handling the given request."""
        if "alarm" in intent_analysis_result.nouns:
            return 1.0  # Maximum certainty if "alarm" is detected in the user's request
        return 0.0

    def find_parameters(self, action: Action, intent_analysis_result: messages.IntentAnalysisResult) -> Parameters:
        parameters = Parameters()

        # Set an alarm
        if action == Action.SET:
            parameters.alarm_time = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
            for result in intent_analysis_result.numbers:
                if result.next_token == "o'clock":
                    parameters.alarm_time = parameters.alarm_time.replace(hour=result.number_token)
                elif result.next_token == "hours":
                    parameters.alarm_time = parameters.alarm_time.replace(hour=result.number_token)
                elif result.next_token == "minutes":
                    parameters.alarm_time = parameters.alarm_time.replace(minute=result.number_token)
                elif result.next_token == "seconds":
                    parameters.alarm_time = parameters.alarm_time.replace(second=result.number_token)

        # Get the active alarm
        elif action in [Action.GET_ACTIVE, Action.CONTINUE, Action.SKIP]:
            with Session(self.db_engine) as session:
                statement = select(models.ASSActiveAlarm).where(models.ASSActiveAlarm.scheduled_time > datetime.now())
                active_alarm = session.exec(statement).first()
                if active_alarm:
                    parameters.alarm_time = active_alarm.scheduled_time

        return parameters

    def register_alarm(self, parameters: Parameters, user_alarm: bool = False) -> None:
        """Registers an alarm in the database and sets a timer for it."""
        with Session(self.db_engine) as session:
            # Remove any existing alarm as we only support one active alarm at a time
            statement = select(models.ASSActiveAlarm)
            existing_alarm = session.exec(statement).first()
            if existing_alarm:
                session.delete(existing_alarm)

            # Register new alarm
            active_alarm = models.ASSActiveAlarm(
                name="User Alarm" if user_alarm else "Default Cron Alarm",
                scheduled_time=parameters.alarm_time,
            )
            session.add(active_alarm)
            session.commit()
            scheduled_time = active_alarm.scheduled_time
        logger.debug("Alarm set for %s.", scheduled_time)

        # Set a timer for the registered alarm

        self.set_next_alarm(scheduled_time)

    def set_next_alarm(self, scheduled_time: datetime) -> None:
        """Sets the next alarm timer."""
        time_until_alarm = (scheduled_time - datetime.now()).total_seconds()

        with self.timer_lock:
            # Cancel any existing timer
            if self.active_timer:
                self.active_timer.cancel()
                logger.debug("Existing alarm timer canceled before registering a new one.")

            # Register the new timer
            self.active_timer = threading.Timer(time_until_alarm, self.trigger_alarm, [scheduled_time])
            self.active_timer.daemon = True
            self.active_timer.start()
            logger.debug("New alarm set for %s.", scheduled_time)

    def trigger_alarm(self, alarm_time: datetime) -> None:
        """Trigger the webhook for the active alarm."""
        try:
            # Trigger the webhook
            with httpx.Client() as client:
                response = client.post(
                    self.custom_skill_config.webhook_url,
                    json={"message": "Alarm triggered", "alarm_time": alarm_time.isoformat()},
                )
                response.raise_for_status()
                logger.info("Alarm triggered successfully for %s.", alarm_time)
        except httpx.HTTPStatusError as exc:
            logger.error("Failed to trigger alarm: %s %s", exc.response.status_code, exc.response.text)
        except Exception as e:
            logger.error("An error occurred while triggering alarm: %s", str(e))

        # Set the next timer based on cron schedule
        self.set_next_alarm_from_cron()

    def set_next_alarm_from_cron(self) -> None:
        """Calculates and sets the next alarm based on the cron expression in the config."""
        cron_expression = self.custom_skill_config.cron_expression
        now = datetime.now()

        # Parse the next scheduled time using croniter
        cron_iter = croniter(cron_expression, now)
        parameters = Parameters()
        parameters.alarm_time = cron_iter.get_next(datetime)

        # Set a timer for the next alarm
        self.register_alarm(parameters)

    def break_execution(self) -> None:
        """Stops all alarms by deleting them from the database and cancelling the current timer."""
        with Session(self.db_engine) as session:
            statement = select(models.ASSActiveAlarm)
            active_alarms = session.exec(statement).all()
            for alarm in active_alarms:
                session.delete(alarm)
            session.commit()

        with self.timer_lock:
            if self.active_timer:
                self.active_timer.cancel()
                self.active_timer = None
                logger.info("All alarms and timers have been stopped.")

    def continue_execution(self) -> None:
        """Resumes the alarm schedule by calculating the next alarm based on the cron expression and starting the timer."""
        self.set_next_alarm_from_cron()
        logger.debug("Resumed alarm schedule.")

    def skip_alarm(self) -> None:
        """Skips the next alarm by calculating the next one after the immediate next cron iteration."""
        cron_expression = self.custom_skill_config.cron_expression
        now = datetime.now()

        # Parse and skip the next cron iteration
        cron_iter = croniter(cron_expression, now)
        cron_iter.get_next(datetime)  # Skip the next iteration
        next_execution = cron_iter.get_next(datetime)

        parameters = Parameters(alarm_time=next_execution)

        # Set the alarm for the skipped iteration's next time
        self.register_alarm(parameters)
        logger.info("Skipped the next cron iteration and set the alarm for %s.", next_execution)

    def get_answer(self, action: Action, parameters: Parameters) -> str:
        answer = self.action_to_answer[action].render(
            action=action,
            parameters=parameters,
        )
        return answer

    def process_request(self, intent_analysis_result: messages.IntentAnalysisResult) -> None:
        action = Action.find_matching_action(intent_analysis_result.client_request.text)
        if action is None:
            logger.error("Unrecognized action in text: %s", intent_analysis_result.client_request.text)
            return

        parameters = self.find_parameters(action, intent_analysis_result=intent_analysis_result)
        if action == Action.SET:
            self.register_alarm(parameters)
        elif action == Action.HELP:
            pass
        elif action == Action.SKIP:
            self.skip_alarm()
        elif action == Action.BREAK:
            self.break_execution()
        elif action == Action.CONTINUE:
            self.set_next_alarm_from_cron()
        elif action == Action.GET_ACTIVE:
            pass
        else:
            logger.debug("No specific action implemented for action: %s", action)
            return

        answer = self.get_answer(action, parameters)
        self.add_text_to_output_topic(answer, client_request=intent_analysis_result.client_request)
