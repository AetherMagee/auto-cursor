import asyncio
import os
import random
import re
import string
import sys
import ctypes
import subprocess
from typing import Optional

import zendriver as zd
from dotenv import load_dotenv
from loguru import logger
from zendriver.core.browser import Browser

from auth import update_auth
from mail import fetch_email
from reset import reset_machine_ids
from reset_helpers.windows import check_admin

if os.path.exists(".env"):
    load_dotenv()
else:
    logger.error("No .env file found")
    logger.info("Create one using .env.example")
    exit(0)

login_url = "https://authenticator.cursor.sh"
sign_up_url = "https://authenticator.cursor.sh/sign-up"
settings_url = "https://www.cursor.com/settings"


def request_admin_elevation():
    """Request admin privileges if not already running as admin."""
    if check_admin():
        return True
        
    logger.info("Requesting administrator privileges...")
    try:
        if getattr(sys, 'frozen', False):
            # If running as a frozen executable
            script = sys.executable
            args = sys.argv[1:]
        else:
            # If running as a Python script
            script = sys.executable
            args = [sys.argv[0]] + sys.argv[1:]
            
        shell32 = ctypes.windll.shell32
        ret = shell32.ShellExecuteW(None, "runas", script, f'"{" ".join(args)}"', None, 1)
        if ret <= 32:  # ShellExecute returns a value <= 32 on error
            logger.error("Failed to elevate privileges. Error code: {}", ret)
            return False
        return True
    except Exception as e:
        logger.error("Failed to request administrator privileges: {}", e)
        return False


async def sign_up(browser: Browser, email: str) -> Optional[str]:
    """
    Registers a new Cursor account.
    Requires user interaction.

    Returns:
    The session token on success or None on failure.
    """

    tab = await browser.get(sign_up_url)

    first_name = random.choice(string.ascii_uppercase) + "".join(random.choices(string.ascii_lowercase, k=5))
    last_name = random.choice(string.ascii_uppercase) + "".join(random.choices(string.ascii_lowercase, k=5))
    password = ''.join(random.choices(string.ascii_uppercase + string.digits + string.punctuation, k=12))

    logger.debug(f"First name: {first_name} | Last name: {last_name} | Password: {password} | Email: {email}")

    logger.info("Inputting fields...")
    first_name_box = await tab.select("input[name='first_name']")
    await first_name_box.send_keys(first_name)

    last_name_box = await tab.select("input[name='last_name']")
    await last_name_box.send_keys(last_name)

    email_box = await tab.select("input[name='email']")
    await email_box.send_keys(email)

    await asyncio.sleep(1)

    logger.info("Submitting...")
    submit_button = await tab.select("button[type='submit']")
    await submit_button.click()

    password_box = None
    logger.info("Waiting for turnstile completion...")
    while not password_box:
        try:
            password_box = await tab.select("input[name='password']", timeout=1)
        except Exception:
            pass

    logger.info("Entering the password...")
    await password_box.send_keys(password)

    logger.info("Submitting...")
    submit_button = await tab.select("button[type='submit']")
    await submit_button.click()

    code_input_box = None
    logger.info("Waiting for turnstile completion...")
    while not code_input_box:
        try:
            code_input_box = await tab.select("input[name='code']", timeout=1)
        except Exception:
            pass

    email_content = await fetch_email(
        os.getenv("IMAP_SERVER", ""),
        int(os.getenv("IMAP_PORT", "0")),
        os.getenv("IMAP_USER", ""),
        os.getenv("IMAP_PASS", ""),
    )
    
    if email_content is None:
        logger.error("Failed to fetch email")
        return None

    code_search = re.search(r'\b\d{6}\b', email_content)
    if not code_search:
        logger.error("Couldn't find a code in your email.")
        return None

    code = code_search.group(0)

    logger.info("Got verification code: " + code)

    await code_input_box.send_keys(code)

    await tab.wait(5)

    cookies = await browser.cookies.get_all()

    for cookie in cookies:
        if cookie.name == "WorkosCursorSessionToken" and cookie.value:
            return cookie.value.split("%3A%3A")[1]

    return None


async def main():
    logger.info("Starting...")

    # Check for admin rights at startup
    if not check_admin():
        if not request_admin_elevation():
            logger.error("This program requires administrator privileges.")
            sys.exit(1)
        sys.exit(0)  # Exit the non-elevated instance

    browser = await zd.start(
        lang="en_US", headless=False,
    )

    email = os.getenv('EMAIL_ADDRESS_PREFIX', 'cur') + "".join(random.choices(string.ascii_lowercase, k=6)) + "@" + os.getenv("DOMAIN", "example.com")

    session_token = await sign_up(browser, email)
    if not session_token:
        logger.error("Couldn't find a session token.")
        exit(1)

    await browser.stop()

    logger.info("Updating the local Cursor database...")
    success = await update_auth(email, session_token, session_token)
    if not success:
        logger.error("Couldn't update the local Cursor database.")
        exit(1)

    logger.info("Resetting machine IDs...")
    success = reset_machine_ids()
    if not success:
        logger.error("Failed to reset machine IDs.")
        exit(1)

    logger.success("Complete!")
    await asyncio.sleep(1)
    input("Press enter to exit...")


if __name__ == "__main__":
    asyncio.run(main())