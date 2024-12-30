import asyncio
import enum
import logging
import string
from datetime import datetime, timedelta
from typing import Self

import aiomqtt
import httpx
import jinja2
from croniter import croniter
from private_assistant_commons import BaseSkill, messages
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from private_assistant_alarm_scheduler_skill import config, models


class Parameters(BaseModel):
    alarm_time: datetime | None = None
    alarm_name: str = "Default Cron Alarm"


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
        mqtt_client: aiomqtt.Client,
        db_engine: AsyncEngine,
        template_env: jinja2.Environment,
        task_group: asyncio.TaskGroup,
        logger: logging.Logger,
    ) -> None:
        super().__init__(config_obj, mqtt_client, task_group, logger=logger)
        self.config_obj: config.SkillConfig = config_obj
        self.db_engine: AsyncEngine = db_engine
        self.template_env = template_env
        self._active_alarm_task: asyncio.Task | None = None
        self.action_to_answer: dict[Action, jinja2.Template] = {
            Action.HELP: template_env.get_template("help.j2"),
            Action.SET: template_env.get_template("set.j2"),
            Action.GET_ACTIVE: template_env.get_template("get_active.j2"),
            Action.SKIP: template_env.get_template("skip.j2"),
            Action.BREAK: template_env.get_template("break.j2"),
            Action.CONTINUE: template_env.get_template("continue.j2"),
        }

    async def calculate_certainty(self, intent_analysis_result: messages.IntentAnalysisResult) -> float:
        """Calculate how confident the skill is about handling the given request."""
        if "alarm" in intent_analysis_result.nouns:
            return 1.0  # Maximum certainty if "alarm" is detected in the user's request
        return 0.0

    async def skill_preparations(self) -> None:
        async with AsyncSession(self.db_engine) as session:
            statement = select(models.ASSActiveAlarm).where(models.ASSActiveAlarm.scheduled_time > datetime.now())
            query_result = await session.exec(statement)
            active_alarm = query_result.first()
            if active_alarm:
                self.logger.info("Active alarm found from previous session, starting alarm task.")
                self.set_next_alarm(active_alarm.scheduled_time)

    async def find_parameters(
        self, action: Action, intent_analysis_result: messages.IntentAnalysisResult
    ) -> Parameters:
        parameters = Parameters()

        # Set an alarm
        if action == Action.SET:
            parameters.alarm_name = "User Alarm"
            parameters.alarm_time = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
            for result in intent_analysis_result.numbers:
                if result.next_token == "o'clock" or result.next_token == "hours":
                    parameters.alarm_time = parameters.alarm_time.replace(hour=result.number_token)
                elif result.next_token == "minutes":
                    parameters.alarm_time = parameters.alarm_time.replace(minute=result.number_token)
                elif result.next_token == "seconds":
                    parameters.alarm_time = parameters.alarm_time.replace(second=result.number_token)
            if parameters.alarm_time < datetime.now():
                parameters.alarm_time += timedelta(days=1)

        elif action == Action.GET_ACTIVE:
            # Retrieve the currently active alarm from the database
            async with AsyncSession(self.db_engine) as session:
                statement = select(models.ASSActiveAlarm).where(models.ASSActiveAlarm.scheduled_time > datetime.now())
                query_result = await session.exec(statement)
                active_alarm = query_result.first()
                if active_alarm:
                    parameters.alarm_time = active_alarm.scheduled_time

        elif action == Action.CONTINUE:
            # Calculate the next alarm time based on the cron expression
            parameters.alarm_time = self.calculate_next_cron_execution(skip_next=False)

        elif action == Action.SKIP:
            # Calculate the alarm time after the next occurrence
            parameters.alarm_time = self.calculate_next_cron_execution(skip_next=True)

        return parameters

    def calculate_next_cron_execution(self, skip_next: bool = False) -> datetime:
        """Calculates the next cron-based execution time.

        Args:
            skip_next (bool): If True, skip the next cron occurrence and return the one after that.

        Returns:
            datetime: The calculated next execution time.
        """
        cron_expression = self.config_obj.cron_expression
        now = datetime.now()
        cron_iter = croniter(cron_expression, now)

        # Skip the next occurrence if needed
        if skip_next:
            cron_iter.get_next(datetime)

        return cron_iter.get_next(datetime)  # type: ignore

    async def register_alarm(self, parameters: Parameters) -> None:
        async with AsyncSession(self.db_engine) as session:
            # Remove any existing alarm as we only support one active alarm at a time
            statement = select(models.ASSActiveAlarm)
            query_result = await session.exec(statement)
            existing_alarm = query_result.first()
            if existing_alarm:
                await session.delete(existing_alarm)

            # Register new alarm
            active_alarm = models.ASSActiveAlarm(
                name=parameters.alarm_name,
                scheduled_time=parameters.alarm_time,
            )
            session.add(active_alarm)
            scheduled_time = active_alarm.scheduled_time
            await session.commit()
            self.logger.debug("Alarm set for %s.", scheduled_time)

        self.set_next_alarm(scheduled_time)

    def set_next_alarm(self, scheduled_time: datetime) -> None:
        time_until_alarm = (scheduled_time - datetime.now()).total_seconds()

        # Cancel any existing timer task
        if self._active_alarm_task:
            self._active_alarm_task.cancel()
            self.logger.debug("Existing alarm timer task canceled.")

        # Create a new task to trigger the alarm after the given delay
        self._active_alarm_task = self.add_task(self.trigger_alarm_after_delay(time_until_alarm))

    async def trigger_alarm_after_delay(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self.trigger_alarm()

    async def trigger_alarm(self) -> None:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.config_obj.webhook_url,
                    json={"message": "Alarm triggered", "alarm_time": datetime.now().isoformat()},
                )
                response.raise_for_status()
                self.logger.info("Alarm triggered successfully.")
        except httpx.HTTPStatusError as exc:
            self.logger.error("Failed to trigger alarm: %s %s", exc.response.status_code, exc.response.text)
        except Exception as e:
            self.logger.error("An error occurred while triggering alarm: %s", str(e))

        # Set the next timer based on cron schedule
        await self.set_next_alarm_from_cron()

    async def set_next_alarm_from_cron(self) -> None:
        """Sets the next alarm based on the cron schedule."""
        # Calculate the next cron-based execution time
        next_execution = self.calculate_next_cron_execution(skip_next=False)
        parameters = Parameters(alarm_time=next_execution)
        await self.register_alarm(parameters)
        self.logger.info("Setting next cron iteration as alarm %s.", next_execution)

    async def break_execution(self) -> None:
        async with AsyncSession(self.db_engine) as session:
            statement = select(models.ASSActiveAlarm)
            query_result = await session.exec(statement)
            active_alarms = query_result.all()
            for alarm in active_alarms:
                await session.delete(alarm)
            await session.commit()

        if self._active_alarm_task:
            self._active_alarm_task.cancel()
            self._active_alarm_task = None
            self.logger.info("All alarms and timers have been stopped.")

    async def skip_alarm(self) -> None:
        """Skips the next occurrence of the cron and sets the alarm for the one after."""
        # Calculate the alarm time after skipping the next occurrence
        second_next_execution = self.calculate_next_cron_execution(skip_next=True)
        parameters = Parameters(alarm_time=second_next_execution)
        await self.register_alarm(parameters)
        self.logger.info("Skipped the next cron iteration and set the alarm for %s.", second_next_execution)

    def get_answer(self, action: Action, parameters: Parameters) -> str:
        template = self.action_to_answer.get(action)
        if template:
            answer = template.render(
                action=action,
                parameters=parameters,
            )
            self.logger.debug("Generated answer using template for action %s.", action)
            return answer
        self.logger.error("No template found for action %s.", action)
        return "Sorry, I couldn't process your request."

    async def process_request(self, intent_analysis_result: messages.IntentAnalysisResult) -> None:
        action = Action.find_matching_action(intent_analysis_result.client_request.text)
        if action is None:
            self.logger.error("Unrecognized action in text: %s", intent_analysis_result.client_request.text)
            return

        parameters = await self.find_parameters(action, intent_analysis_result=intent_analysis_result)

        if action == Action.SET:
            await self.register_alarm(parameters)
        elif action == Action.HELP:
            pass
        elif action == Action.SKIP:
            self.add_task(self.skip_alarm())
        elif action == Action.BREAK:
            self.add_task(self.break_execution())
        elif action == Action.CONTINUE:
            self.add_task(self.set_next_alarm_from_cron())
        elif action == Action.GET_ACTIVE:
            pass
        else:
            self.logger.debug("No specific action implemented for action: %s", action)
            return

        answer = self.get_answer(action, parameters)
        self.add_task(self.send_response(answer, client_request=intent_analysis_result.client_request))
