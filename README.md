# Ivend Summary Report

This is a Django project for generating summary reports for Ivend machines.

## Prerequisites

- Python 3.x
- Django

## Setup

1. **Clone the repository:**
   ```bash
   git clone <repository_url>
   cd summary_report
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   *(Note: create a `requirements.txt` if one does not exist)*

4. **Apply migrations:**
   ```bash
   python manage.py migrate
   ```

5. **Run the development server:**
   ```bash
   python manage.py runserver
   ```

## Project Structure

- `ops_dashboard/`: Main application directory.
- `logistics/`: App for handling logistics data.
- `media/`: Directory for uploaded media files.
- `db.sqlite3`: Local SQLite database.

## Usage

Access the application at `http://127.0.0.1:8000/`.
