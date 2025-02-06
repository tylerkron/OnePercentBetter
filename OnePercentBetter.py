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
def get_sheet(sheet_name="Daily Tracker"):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("OnePercentBetter Google Sheets.json", scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open("TK Health Tracker")
    
    # Create sheets if they don't exist
    sheet_names = [sheet.title for sheet in spreadsheet.worksheets()]
    if sheet_name not in sheet_names:
        if sheet_name == "Goals":
            sheet = spreadsheet.add_worksheet(title="Goals", rows=100, cols=2)
            # Set up goals sheet headers
            sheet.update('A1:B1', [['Metric', 'Target']])
        elif sheet_name == "Daily Tracker":
            sheet = spreadsheet.add_worksheet(title="Daily Tracker", rows=1000, cols=6)
            sheet.update('A1:F1', [['Date', 'Pushups', 'Steps', 'Sleep Score', 'Sleep Duration', 'Worked Out']])
    
    return spreadsheet.worksheet(sheet_name)

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
        date_to_query = date  # Date is already in the correct format from the AI
    for record in records:
        if record.get("Date") == date_to_query:
            return f"Your {metric} for {date_to_query} was {record.get(metric, 'N/A')}."
    return f"No data found for {date_to_query}."

# Log data into Google Sheets
def log_data(log_details):
    sheet = get_sheet()
    expected_headers = ["Date", "Pushups", "Steps", "Sleep Score", "Sleep Duration", "Worked Out"]
    records = sheet.get_all_records(expected_headers=expected_headers)
    
    # Handle date based on the input
    date = log_details.get('date', 'today')
    if date == 'today':
        date_str = datetime.now().strftime("%-m/%-d/%Y")
    elif date == 'yesterday':
        date_str = (datetime.now() - timedelta(days=1)).strftime("%-m/%-d/%Y")
    else:
        date_str = date  # Date is already in the correct format from the AI
    
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

def set_goal(goal_details):
    sheet = get_sheet("Goals")
    metric = goal_details.get('metric')
    target = goal_details.get('target')
    
    # Check if goal already exists for this metric
    goals = sheet.get_all_records()
    for idx, goal in enumerate(goals, start=2):  # start=2 because of header row
        if goal['Metric'] == metric:
            # Update existing goal
            sheet.update(f'B{idx}', target)
            return f"Updated daily goal for {metric}: {target}"
    
    # Add new goal if it doesn't exist
    new_row = [metric, target]
    sheet.append_row(new_row)
    return f"Set new daily goal for {metric}: {target}"

def check_goals_progress(timeframe="today"):
    goals_sheet = get_sheet("Goals")
    daily_sheet = get_sheet("Daily Tracker")
    
    # Get all goals
    goals = goals_sheet.get_all_records()
    
    # Get dates for the requested timeframe
    today = datetime.now()
    if timeframe == "today":
        start_date = today
        days_to_check = 1
    elif timeframe == "week":
        # Start from beginning of current week (Monday)
        start_date = today - timedelta(days=today.weekday())
        days_to_check = 7
    elif timeframe == "month":
        # Start from beginning of current month
        start_date = today.replace(day=1)
        # Calculate days in current month
        next_month = today.replace(day=28) + timedelta(days=4)
        days_to_check = (next_month - timedelta(days=next_month.day)).day
    
    # Get all records within the timeframe
    daily_records = daily_sheet.get_all_records()
    relevant_records = []
    for i in range(days_to_check):
        date = start_date + timedelta(days=i)
        date_str = date.strftime("%-m/%-d/%Y")
        record = next((r for r in daily_records if r['Date'] == date_str), None)
        if record:
            relevant_records.append(record)
    
    progress_report = []
    for goal in goals:
        metric = goal['Metric']
        daily_target = float(goal['Target']) if str(goal['Target']).replace('.', '').isdigit() else goal['Target']
        
        if timeframe == "today":
            period_target = daily_target
        elif timeframe == "week":
            period_target = daily_target * 7
        else:  # month
            period_target = daily_target * days_to_check
            
        # Calculate current progress
        current_total = 0
        for record in relevant_records:
            if metric in record and isinstance(record[metric], (int, float)):
                current_total += record[metric]
        
        # Format the progress message
        if isinstance(daily_target, (int, float)):
            progress = (current_total / period_target) * 100 if period_target > 0 else 0
            period_type = "today" if timeframe == "today" else f"this {timeframe}"
            progress_report.append(
                f"{metric}: {current_total}/{period_target} for {period_type} ({progress:.1f}%)\n"
                f"Daily goal: {daily_target}"
            )
        else:
            progress_report.append(f"{metric}: {current_total}/{daily_target} (non-numeric goal)")
    
    if not progress_report:
        return "No goals found. Set some goals to track your progress!"
    
    timeframe_msg = "Today's" if timeframe == "today" else f"This {timeframe}'s"
    return f"{timeframe_msg} Progress:\n" + "\n\n".join(progress_report)

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
            Classify messages into one of these intents: 'query', 'log', 'set_goal', or 'check_goals'.
            
            IMPORTANT DATE HANDLING:
            - For any dates mentioned in the user's message, convert them to the format M/D/YYYY (no leading zeros)
            - Examples of date conversions:
              • "2/1/25" -> "2/1/2025"
              • "02/01/25" -> "2/1/2025"
              • "2-1-25" -> "2/1/2025"
              • "02-01-2025" -> "2/1/2025"
            - Special keywords "today" and "yesterday" should be passed through as-is
            
            For 'log', classify whether each metric is **set** or **incremented**.
            - If the user says **"set sleep score to 85 and add 50 pushups"**, return:
            - <Pushups value="50" increment="true" />
            - <SleepScore value="85" increment="false" />
            
            For 'log', provide the output in XML format:
            <response>
                <intent>log</intent>
                <details>
                    <date>today</date>  <!-- Can be 'today', 'yesterday', or a date in M/D/YYYY format -->
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
                    <date>yesterday</date>  <!-- Can be 'today', 'yesterday', or a date in M/D/YYYY format -->
                </details>
            </response>
            
            For 'set_goal', provide the output in XML format (note: all goals are daily):
            <response>
                <intent>set_goal</intent>
                <details>
                    <metric>Steps</metric>
                    <target>10000</target>
                </details>
            </response>
            
            For 'check_goals', provide the output in XML format:
            <response>
                <intent>check_goals</intent>
                <details>
                    <timeframe>today</timeframe>
                </details>
            </response>
            Note for check_goals: timeframe can be 'today', 'week', or 'month'
            
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
        elif intent == "set_goal":
            goal_data = {
                "metric": details.find("metric").text,
                "target": details.find("target").text,
            }
            response_message = set_goal(goal_data)
        elif intent == "check_goals":
            timeframe = details.find("timeframe").text
            response_message = check_goals_progress(timeframe)
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
        "- Query your data by asking 'What was my sleep score yesterday?'\n"
        "- Set daily goals by saying 'Set a goal of 10000 steps' or 'I want to do 100 pushups per day'\n"
        "- Check your progress by asking:\n"
        "  • 'How am I doing on my goals today?'\n"
        "  • 'Show me my progress this week'\n"
        "  • 'What's my monthly progress?'\n"
        "\nI'll help you stay on track and get 1% better every day! 💪")
    
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
    