"""Constants for the CP Trains integration."""
from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "cp_trains"
CONF_TRAIN_NUMBER = "train_number"
UPDATE_INTERVAL_SECONDS = 60

API_URL = "https://www.infraestruturasdeportugal.pt/negocios-e-servicos/horarios-ncombio/{train_number}/{train_date}"
