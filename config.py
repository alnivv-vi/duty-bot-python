from dotenv import load_dotenv, find_dotenv
import os

load_dotenv(find_dotenv(filename=".env.local"), override=True)

LOOP_BOT_TOKEN = os.getenv("LOOP_BOT_TOKEN")
LOOP_URL = os.getenv("LOOP_URL")
CHANNEL_ID = os.getenv("REPORT_PROD_CHANNEL_ID")
PORT = os.getenv("PORT")
