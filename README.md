# Brightloop Subscription Management System

A unified web platform that manages the full lifecycle of subscription billing. Built to replace manual spreadsheet tracking, this system handles product catalogs, self-service customer purchasing, automated invoice generation, payment reconciliation, and financial dashboards.

This project was built as an **8-hour time-boxed MVP** by a 3-person team using **Python, Flask, and MySQL**.

---

## 🌟 Key Features & Role-Based Access

The system utilizes a strict **3-Tier Role System** to ensure data security and operational efficiency:

### 1. Admin (System Control)
* **Catalog Management:** Create and manage Software Products and Billing Plans (Monthly/Yearly).
* **Financial Dashboard:** View real-time metrics (Active Subscriptions, Total Invoiced, Payments Received, Outstanding Balance, Overdue Invoices).
* **Billing Operations:** Generate invoices from subscriptions, record incoming payments, and manage global system settings.

### 2. Internal Staff / Support (Day-to-Day Operations)
* **Customer Directory:** Search and view all registered customer accounts.
* **Billing Support:** View specific customer profiles, check their active subscriptions, and review their invoice/payment history.
* **Assisted Payments:** Record payments on behalf of customers to resolve billing queries over the phone/email.

### 3. Customer (Self-Service Portal)
* **Self-Registration:** Secure account creation with hashed passwords.
* **Software Marketplace:** Browse available products and 1-click subscribe to billing plans.
* **My Portal:** View active subscriptions, track expiry dates, and view personal invoice history.
* **Self-Management:** 1-click unsubscribe/cancel active subscriptions.

---

## 🛠️ Tech Stack
* **Backend:** Python 3, Flask, Flask-SQLAlchemy, Flask-Login, Flask-Migrate
* **Database:** MySQL (via PyMySQL)
* **Frontend:** Jinja2 Templates, Bootstrap 5, Bootstrap Icons
* **Security:** Werkzeug (Password Hashing), Role-Based Access Control (RBAC) guards.

---

## 🚀 Local Setup & Installation

### Prerequisites
* Python 3.8+
* MySQL Server (XAMPP, WAMP, or standalone MySQL Workbench)

### Step 1: Database Configuration
1. Start your MySQL server.
2. Open your MySQL terminal or phpMyAdmin and create the database:
   ```sql
   CREATE DATABASE subscription_db;
Open config.py and ensure your SQLALCHEMY_DATABASE_URI matches your local MySQL credentials:
   # Example for XAMPP default (no password):
   SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://root@127.0.0.1:3306/subscription_db'

### Step 2: Environment Setup

# 1. Clone the repository
git clone <your-repo-url>
cd Subscription-Management-System_odoo

# 2. Create and activate a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

### Step 3: initialize & Seed Database

# Set Flask environment variables
set FLASK_APP=app.py
set FLASK_ENV=development

# Create tables in MySQL
flask init-db
# (Alternatively, run: python reset_db.py)

# Seed demo Admin, Customer, Products, and Plans
flask seed-db


### Step 4: Run the application

python app.py

Open your browser and navigate to http://127.0.0.1:5000

## 👥 Team Architecture
This application was built using a parallel development strategy with strict module ownership to prevent merge conflicts:
Ephrem Shajan Madassery (Foundation & Catalog): App factory, MySQL configuration, Authentication, User Models, Product/Plan CRUD, and Base Layout.
Irene Sara Philipose (Subscriptions & Invoices): Subscription lifecycle, Line Items, Invoice Generation, Snapshot Pricing, and Duplicate Billing Prevention.
Amal Vinayan (Payments & Dashboard): Payment Recording, Outstanding Balance Calculation, Overdue Logic, Financial Dashboard, and End-to-End Integration Testing.
