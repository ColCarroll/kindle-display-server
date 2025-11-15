#!/usr/bin/env python3
"""
Google Calendar Setup Script

This script helps you authenticate with Google Calendar and save the credentials.

Prerequisites:
1. Go to https://console.cloud.google.com/
2. Create a new project (or select existing)
3. Enable Google Calendar API
4. Create OAuth 2.0 credentials (Desktop app)
5. Download the credentials.json file to this directory
"""

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Scopes - readonly access to calendar
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

def setup_calendar():
    """Run the OAuth flow and save credentials"""

    credentials_file = 'credentials.json'
    token_file = 'token.json'

    if not os.path.exists(credentials_file):
        print(f"❌ Error: {credentials_file} not found!")
        print("\nTo get credentials.json:")
        print("1. Go to https://console.cloud.google.com/")
        print("2. Create a new project (or select existing)")
        print("3. Enable Google Calendar API:")
        print("   - Go to 'APIs & Services' > 'Library'")
        print("   - Search for 'Google Calendar API'")
        print("   - Click 'Enable'")
        print("4. Create OAuth 2.0 credentials:")
        print("   - Go to 'APIs & Services' > 'Credentials'")
        print("   - Click 'Create Credentials' > 'OAuth client ID'")
        print("   - Choose 'Desktop app'")
        print("   - Download the JSON file and save it as 'credentials.json'")
        return False

    creds = None

    # Check if we already have a token
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    # If no valid credentials, run OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired token...")
            creds.refresh(Request())
        else:
            print("Starting OAuth flow...")
            print("Your browser will open. Please authorize the application.")
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the credentials
        with open(token_file, 'w') as token:
            token.write(creds.to_json())
        print(f"✅ Credentials saved to {token_file}")

    # Test the connection
    try:
        print("\nTesting connection to Google Calendar...")
        service = build('calendar', 'v3', credentials=creds)

        # Get the user's calendar list
        calendar_list = service.calendarList().list().execute()

        print("\n✅ Successfully connected to Google Calendar!")
        print("\nYour calendars:")
        for calendar in calendar_list.get('items', []):
            print(f"  - {calendar['summary']} (ID: {calendar['id']})")

        print("\n📝 Next steps:")
        print("1. Add this line to your .env file:")
        print(f"   GOOGLE_CALENDAR_TOKEN_FILE={os.path.abspath(token_file)}")
        print("2. Choose which calendar to use and add:")
        print("   GOOGLE_CALENDAR_ID=<calendar-id>")
        print("   (Use 'primary' for your main calendar)")

        return True

    except Exception as e:
        print(f"❌ Error testing connection: {e}")
        return False

if __name__ == '__main__':
    print("=" * 60)
    print("Google Calendar Setup for Kindle Display")
    print("=" * 60)
    print()

    success = setup_calendar()

    if success:
        print("\n✅ Setup complete!")
    else:
        print("\n❌ Setup failed. Please check the instructions above.")
