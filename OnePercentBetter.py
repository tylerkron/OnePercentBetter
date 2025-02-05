import google.generativeai as genai
import xml.etree.ElementTree as ET
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import os

# Who is authorized to use this?
AUTHORIZED_USERS = [int(user) for user in os.getenv("AUTHORIZED_USERS", "").split(",") if user.strip().isdigit()]

# Configure Environment Variables
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")

# Configure Google Sheets API
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("OnePercentBetter Google Sheets.json", scope)
    client = gspread.authorize(creds)
    return client.open("TK Health Tracker").sheet1

# Query data from Google Sheets
def query_data(query_details):
    sheet = get_sheet()
    expected_headers = ["Date", "Pushups", "Steps", "Sleep Score", "Sleep Duration", "Worked Out"]
    records = sheet.get_all_records(expected_headers=expected_headers)
    metric = query_details.get("metric")
    date = query_details.get("date")
    if date == "yesterday":
        date_to_query = (datetime.now() - timedelta(days=1)).strftime("%-m/%-d/%Y")
    elif date == "today":
        date_to_query = datetime.now().strftime("%-m/%-d/%Y")
    else:
        date_to_query = date  # Specific date in format MM/DD/YYYY
    for record in records:
        if record.get("Date") == date_to_query:
            return f"Your {metric} for {date_to_query} was {record.get(metric, 'N/A')}."
    return f"No data found for {date_to_query}."

# Log data into Google Sheets
def log_data(log_details):
    sheet = get_sheet()
    expected_headers = ["Date", "Pushups", "Steps", "Sleep Score", "Sleep Duration", "Worked Out"]
    records = sheet.get_all_records(expected_headers=expected_headers)
    date_str = datetime.now().strftime("%-m/%-d/%Y")
    metrics = log_details['metrics']
    metric_mapping = {
        "Pushups": "Pushups",
        "Steps": "Steps",
        "SleepScore": "Sleep Score",  # Convert XML 'SleepScore' to 'Sleep Score'
        "SleepDuration": "Sleep Duration",
        "WorkedOut": "Worked Out"
    }
    row_index = None
    for i, record in enumerate(records, start=2):
        if record.get('Date') == date_str:
            row_index = i
            break
    if row_index:
        for key, value in metrics.items():
            metric_name = metric_mapping.get(key, key)
            metric_value_str = str(value.get("value"))  # Ensure it's a string
            metric_value = int(metric_value_str) if metric_value_str.isdigit() else metric_value_str
            increment = value.get("increment") == "true"
            if metric_name in expected_headers:
                col_index = expected_headers.index(metric_name) + 1
                if increment:
                    try:
                        current_value = int(sheet.cell(row_index, col_index).value or 0)
                        new_value = current_value + metric_value
                    except ValueError:
                        new_value = metric_value  # Default to setting if invalid number
                else:
                    new_value = metric_value  # Direct set
                sheet.update_cell(row_index, col_index, new_value)
        return f"Updated data for {date_str}: {metrics}."
    new_row = [
        date_str,
        metrics.get("Pushups", {}).get("value", 0),
        metrics.get("Steps", {}).get("value", 0),
        metrics.get("Sleep Score", {}).get("value", 0),
        metrics.get("Sleep Duration", {}).get("value", "00:00:00"),
        metrics.get("Worked Out", {}).get("value", 'N')
    ]
    sheet.append_row(new_row)
    return f"Logged new data for {date_str}: {metrics}."

# Handle user messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")
        return
    user_message = update.message.text
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(f"""
            You are a system that determines user intent for fitness data.
            Classify messages into one of two intents: 'query' or 'log'.
            For 'log', classify whether each metric is **set** or **incremented**.
            - If the user says **"set sleep score to 85 and add 50 pushups"**, return:
            - <Pushups value="50" increment="true" />
            - <SleepScore value="85" increment="false" />
            For 'log', provide the output in XML format:
            <response>
                <intent>log</intent>
                <details>
                    <date>today</date>
                    <metrics>
                        <Pushups value="50" increment="true" />
                        <Steps value="1000" increment="true" />
                        <SleepScore value="85" increment="false" />
                    </metrics>
                </details>
            </response>
            For 'query', provide the output in XML format:
            <response>
                <intent>query</intent>
                <details>
                    <metric>Sleep Score</metric>
                    <date>yesterday</date>
                </details>
            </response>
            Always return a valid XML response.
            For sleep duration, always return it in the format HH:MM:SS.
            For workout status, return 'Y' if the user worked out and 'N' otherwise.
            Classify this message: '{user_message}'
            """)
        
        # Extract valid XML from response
        xml_match = re.search(r"```xml\n(.*?)\n```", response.text, re.DOTALL)
        xml_content = xml_match.group(1) if xml_match else response.text
        root = ET.fromstring(xml_content)
        intent = root.find("intent").text
        details = root.find("details")
        if intent == "query":
            intent_data = {
                "intent": "query",
                "details": {
                    "metric": details.find("metric").text,
                    "date": details.find("date").text
                }
            }
            response_message = query_data(intent_data["details"])
        elif intent == "log":
            metrics = {}
            for metric in details.find("metrics"):
                metric_name = metric.tag
                metric_value = metric.get("value")  # Extracts the value
                metric_increment = metric.get("increment", "false")  # Default is false
                if metric_name == "SleepDuration":
                    metrics[metric_name] = {"value": metric_value}  # Store as HH:MM:SS string
                elif metric_name == "WorkedOut":
                    metrics[metric_name] = {"value": metric_value}  # Already formatted as 'Y' or 'N'
                else:
                    # Ensure metric_value is a string before checking isdigit()
                    metric_value = str(metric_value)
                    if metric_value.isdigit():
                        metric_value = int(metric_value)  # Convert to int only if it's a valid number
                    metrics[metric_name] = {"value": metric_value, "increment": metric_increment}
            intent_data = {
                "intent": "log",
                "details": {
                    "date": details.find("date").text,
                    "metrics": metrics
                }
            }
            response_message = log_data(intent_data["details"])
        else:
            response_message = "I couldn't determine your intent. Please clarify."
        await update.message.reply_text(response_message)
    except Exception as e:
        await update.message.reply_text(f"An error occurred while processing your request: {e}")

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! You can:\n"
        "- Log your data by saying 'I did 50 pushups today.'\n"
        "- Query your data by asking 'What was my sleep score yesterday?'")
    
# Main function
def main():
    if not telegram_token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set.")
    application = Application.builder().token(telegram_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()
if __name__ == "__main__":
    main()
    