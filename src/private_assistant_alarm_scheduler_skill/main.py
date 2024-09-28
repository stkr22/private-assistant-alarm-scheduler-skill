import pathlib
from typing import Annotated

import jinja2
import paho.mqtt.client as mqtt
import typer
from private_assistant_commons import skill_config
from sqlmodel import SQLModel, create_engine

from private_assistant_alarm_scheduler_skill import alarm_scheduler_skill, config

app = typer.Typer()


@app.command()
def start_skill(
    config_path: Annotated[pathlib.Path, typer.Argument(envvar="PRIVATE_ASSISTANT_CONFIG_PATH")],
):
    config_obj = skill_config.load_config(config_path, config.SkillConfig)
    db_engine = create_engine(skill_config.PostgresConfig.from_env().connection_string)
    SQLModel.metadata.create_all(db_engine)
    time_skill_obj = alarm_scheduler_skill.AlarmSchedulerSkill(
        mqtt_client=mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=config_obj.client_id,
            protocol=mqtt.MQTTv5,
        ),
        config_obj=config_obj,
        template_env=jinja2.Environment(
            loader=jinja2.PackageLoader(
                "private_assistant_alarm_scheduler_skill",
                "templates",
            ),
        ),
        db_engine=db_engine,
    )
    time_skill_obj.run()


if __name__ == "__main__":
    start_skill(config_path=pathlib.Path("./local_config.yaml"))
