# ARCHITECT'S QUICK START GUIDE
## Entelim Platform - Get Up and Running

> ## ⚠️ LEGACY DOCUMENT — SETUP STEPS ARE WRONG FOR THIS REPOSITORY
>
> The feature tour below is still broadly useful. **The setup, database and command
> sections are not.** Specifically:
>
> - It describes Flask-Migrate (`flask db upgrade`, "18 migrations") and
>   `python manage.py recreate_db|setup_dev|runserver`. The current path is
>   `flask --app manage init-db` then `flask --app manage reconcile-schema`, and
>   `recreate-db` is destructive and now requires `--force`. See
>   [ADR 0002](docs/adr/0002-schema-management.md).
> - It hardcodes PostgreSQL on **port 5439**; use whatever `DATABASE_URL` specifies.
> - It prints default credentials (`admin@example.com` / `admin123`) — these come from
>   `ADMIN_EMAIL` / `ADMIN_PASSWORD` in your `.env`.
> - It references `SECURITY_REMEDIATION.md`, `KNOWN_ISSUES.md`,
>   `DEPLOYMENT_READINESS_GOVERNANCE.md` and `docs/monitoring/grafana_dashboard.json`,
>   none of which exist here.
> - Support channels ("#archie-support", internal wiki) are placeholders. For this
>   open-source project use `CONTRIBUTING.md` and `SECURITY.md`.
>
> **For setup follow [`README.md`](README.md); for working in the codebase follow
> [`CLAUDE.md`](CLAUDE.md).**

**Last Updated:** 2026-02-19  
**Target Audience:** Enterprise Architects  
**Time to Complete:** 15-30 minutes  
**Difficulty:** Intermediate

---

## OVERVIEW

Entelim (formerly the Architecture Repository & Capability Hub with Integrated Expertise) is your enterprise architecture management platform. This guide gets you from zero to productive in 30 minutes.

### What Can You Do With Entelim?

✅ **Application Portfolio Management** - Track 1000+ enterprise applications  
✅ **Vendor & Technology Analysis** - Manage vendor relationships and licenses  
✅ **Business Capability Mapping** - Map applications to business capabilities  
✅ **AI-Powered Architecture Assistant** - Get intelligent recommendations  
✅ **ArchiMate Modeling** - Full ArchiMate 3.2 support  
✅ **Strategic Planning** - Roadmaps, initiatives, and governance  

---

## PREREQUISITES

### Required
- ✅ **Windows 10/11** or Linux/Mac
- ✅ **PostgreSQL 14+** (running on port 5439)
- ✅ **Python 3.13** (or 3.11 for ML features)
- ✅ **Git** installed

### Optional but Recommended
- Redis (for caching and async tasks)
- Docker (for containerized deployment)
- VS Code or PyCharm (for development)

---

## STEP 1: VERIFY YOUR ENVIRONMENT

### Check Python Version
```bash
python --version
# Expected: Python 3.13.0 (or 3.11.x)
```

### Check PostgreSQL
```bash
# Windows
netstat -an | findstr "5439"
# Expected: TCP    127.0.0.1:5439         0.0.0.0:0              LISTENING

# Linux/Mac
lsof -i :5439
# Expected: postgres process listening on port 5439
```

### Verify Virtual Environment
```bash
# Should already exist
ls venv/

# If not, create it:
python -m venv venv

# Activate it:
# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

---

## STEP 2: CONFIGURE ENVIRONMENT

### Create Your .env File
```bash
# .env file already exists with template configuration
# Review it: cat .env (Linux/Mac) or type .env (Windows)

# Key configurations to verify:
```

**Database Configuration:**
```env
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5439/flask_app  # secrets-safety-ok
DEV_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5439/flask_dev  # secrets-safety-ok
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5439/flask_test  # secrets-safety-ok
```

**Flask Configuration:**
```env
SECRET_KEY=<already_set>
FLASK_APP=manage.py
FLASK_ENV=development
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=admin123
```

**Feature Flags (All Enabled by Default):**
```env
USE_GUARDRAILS=true
USE_ARCHITECTURE_GUARDRAILS=true
USE_APPLICATIONS_GUARDRAILS=true
USE_VENDORS_GUARDRAILS=true
USE_AI_CHAT_GUARDRAILS=true
# ... 11 v2 modules total
```

**⚠️ IMPORTANT: API Keys**

The `.env` file contains API keys for LLM services (OpenAI, Anthropic, etc.). These should be rotated before production use. See `SECURITY_REMEDIATION.md` for details.

For development/testing, the existing keys work but monitor usage!

---

## STEP 3: DATABASE SETUP

### Option A: First-Time Setup (Recommended)

```bash
# Activate virtual environment first
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Create database and run migrations
python manage.py recreate_db

# Set up development data (roles, admin user, etc.)
python manage.py setup_dev

# Optional: Seed with example data
python manage.py add_fake_data
```

**What This Does:**
- Creates database schema (162 models)
- Runs all 18 migrations
- Creates admin user (email: admin@example.com, password: admin123)
- Sets up roles and permissions
- Optionally adds sample applications, vendors, capabilities

### Option B: Migrations Only (Existing Database)

```bash
# Check current migration state
flask db current

# Apply pending migrations
flask db upgrade

# If issues, check migration history
flask db history
```

### Verify Database Setup

```bash
# Connect to PostgreSQL
psql -h 127.0.0.1 -p 5439 -U postgres -d flask_dev

# List tables (should see 60+ tables)
\dt

# Count applications
SELECT COUNT(*) FROM applications;

# Exit
\q
```

---

## STEP 4: START THE APPLICATION

### Quick Start (Recommended)

```bash
# Make sure virtual environment is activated
venv\Scripts\activate

# Start Flask development server
python manage.py runserver

# Expected output:
# Loaded environment from: C:\...\archie\.env
# API keys loaded: OPENAI_API_KEY, ANTHROPIC_API_KEY, ...
#  * Running on http://127.0.0.1:5000
#  * Running on http://localhost:5000
```

### Alternative: Using Flask Directly

```bash
# Set environment variables (Windows)
set FLASK_APP=manage.py
set FLASK_ENV=development

# Set environment variables (Linux/Mac)
export FLASK_APP=manage.py
export FLASK_ENV=development

# Run Flask
flask run
```

### Alternative: Using Gunicorn (Production-like)

```bash
gunicorn -w 4 -b 127.0.0.1:5000 "app:create_app('development')"
```

---

## STEP 5: ACCESS THE APPLICATION

### Open Your Browser

```
Primary URL: http://localhost:5000
Alternative:  http://127.0.0.1:5000
```

### Login

**Default Admin Credentials:**
- **Email:** admin@example.com
- **Password:** admin123

**⚠️ Change these immediately after first login!**

Go to: **Account Settings → Change Password**

---

## STEP 6: EXPLORE KEY FEATURES

### 1. Application Portfolio (Core Feature)

**URL:** http://localhost:5000/applications

**What You Can Do:**
- View all applications in your portfolio
- Create new applications
- Edit application details
- Delete applications
- Search and filter
- Export to Excel

**Try It:**
1. Click "Applications" in main navigation
2. Click "New Application" button
3. Fill in details:
   - Name: "My First App"
   - Description: "Test application"
   - Status: "Production"
4. Click "Save"
5. View your new application in the list

---

### 2. Vendor Management

**URL:** http://localhost:5000/vendors

**What You Can Do:**
- Track vendor relationships
- Manage contracts and licenses
- View vendor products
- Analyze vendor dependencies
- Generate vendor reports

**Try It:**
1. Click "Vendors" in main navigation
2. Explore existing vendors (if sample data loaded)
3. Click a vendor to see details
4. Try "Vendor Analysis" feature

---

### 3. Business Capabilities

**URL:** http://localhost:5000/capabilities

**What You Can Do:**
- View capability hierarchy
- Map applications to capabilities
- Analyze coverage gaps
- Track capability maturity
- Generate heat maps

**Try It:**
1. Click "Capabilities" in main navigation
2. Browse the capability tree
3. Select a capability to see mapped applications
4. Try the "Gap Analysis" feature

---

### 4. AI Chat Assistant (Premium Feature)

**URL:** http://localhost:5000/ai-chat

**What You Can Do:**
- Ask architecture questions
- Get technology recommendations
- Analyze vendor options
- Smart search across platform
- Generate documentation

**Available Domains:**
- Architecture Assistant (ArchiMate expert)
- Technology Advisor (stack recommendations)
- Capability Analyst (business capability insights)
- Vendor Intelligence (vendor comparisons)
- Gap Detection (identify architecture gaps)
- Smart Search (intelligent queries)
- General Assistant (multi-domain)

**Try It:**
1. Click "AI Chat" in main navigation
2. Select a domain (try "Architecture Assistant")
3. Ask a question:
   - "What are the key principles of ArchiMate?"
   - "Recommend a tech stack for a web application"
   - "Compare vendor options for ERP systems"
4. View AI-generated response

**⚠️ Note:** AI Chat requires API keys (OpenAI/Anthropic). Monitor your usage!

---

### 5. ArchiMate Modeling

**URL:** http://localhost:5000/architecture

**What You Can Do:**
- Create ArchiMate models
- Define elements and relationships
- Import/export models
- Generate views
- Validate compliance

---

### 6. Strategic Planning

**URL:** http://localhost:5000/strategic

**What You Can Do:**
- Create strategic initiatives
- Build technology roadmaps
- Track investments
- Assess risks
- Generate governance reports

---

## STEP 7: COMMON TASKS

### Create Your First Application

```bash
# Via Web UI:
1. Go to http://localhost:5000/applications
2. Click "New Application"
3. Fill in:
   - Name: "Customer Portal"
   - Description: "Customer-facing web application"
   - Type: "Web Application"
   - Status: "Production"
   - Owner: "IT Department"
4. Click "Save"

# Via API:
curl -X POST http://localhost:5000/api/applications \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Customer Portal",
    "description": "Customer-facing web application",
    "application_type": "Web Application",
    "lifecycle_status": "Production"
  }'
```

### Map Application to Capability

```bash
# Via Web UI:
1. Go to application details page
2. Click "Capabilities" tab
3. Click "Add Capability"
4. Select capability from tree
5. Set coverage level (0-100%)
6. Click "Save"

# This creates the linkage between your application and business capabilities
```

### Run a Vendor Analysis

```bash
# Via Web UI:
1. Go to http://localhost:5000/vendors
2. Select a vendor
3. Click "Analysis" tab
4. View:
   - Dependencies (which apps use this vendor)
   - Contracts (active agreements)
   - Costs (annual spend)
   - Risks (vendor lock-in, compliance)
```

### Generate a Report

```bash
# Via Web UI:
1. Go to Reports section
2. Select report type:
   - Application Inventory
   - Vendor Summary
   - Capability Coverage
   - Technology Stack
3. Choose export format (Excel, PDF, JSON)
4. Click "Generate"
```

---

## STEP 8: TROUBLESHOOTING

### Application Won't Start

**Symptom:** `python manage.py runserver` hangs or errors

**Solutions:**

1. **Database Connection Issue**
```bash
# Check PostgreSQL is running
netstat -an | findstr "5439"  # Windows
lsof -i :5439  # Linux/Mac

# Test connection
psql -h 127.0.0.1 -p 5439 -U postgres -d flask_dev

# If fails, check:
# - PostgreSQL service is running
# - Port 5439 is correct (or change to 5432)
# - Username/password in .env match database
```

2. **Missing Dependencies**
```bash
# Reinstall requirements
pip install -r requirements.txt

# Common issues:
# - psycopg needs PostgreSQL client libraries
# - python-magic-bin needs libmagic (Windows)
```

3. **Migration Issues**
```bash
# Check migration state
flask db current

# Reset if needed (WARNING: destroys data)
python manage.py recreate_db

# Or migrate forward
flask db upgrade
```

### Login Not Working

**Symptom:** "Invalid email or password" error

**Solutions:**

1. **Admin User Not Created**
```bash
# Re-run setup
python manage.py setup_dev

# This recreates admin user with default credentials
```

2. **Wrong Credentials**
- Default email: `admin@example.com`
- Default password: `admin123`
- Check for typos, case-sensitive

3. **Database Issue**
```bash
# Verify user exists
psql -h 127.0.0.1 -p 5439 -U postgres -d flask_dev
SELECT email, role_id FROM users WHERE email = 'admin@example.com';
```

### Page Shows 500 Error

**Symptom:** White page with "Internal Server Error"

**Solutions:**

1. **Check Logs**
```bash
# Console output from manage.py runserver
# Look for Python tracebacks

# Or check log files
tail -f flask_server.log  # Linux/Mac
type flask_server.log  # Windows
```

2. **Common Causes:**
- Missing database table (run migrations)
- Incorrect model relationships
- Missing API keys (if using AI features)
- Service method not implemented (stub function)

3. **Debug Mode**
```bash
# Already enabled in development
# Check .env: FLASK_ENV=development

# Flask will show detailed error page
```

### AI Chat Not Responding

**Symptom:** Chat sends message but no response

**Solutions:**

1. **Check API Keys**
```bash
# Verify keys are set in .env
grep "API_KEY" .env

# Test OpenAI key
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer YOUR_OPENAI_API_KEY"
```

2. **Check Network**
- Firewall blocking OpenAI/Anthropic APIs?
- Proxy configuration needed?
- Rate limit reached?

3. **Check Browser Console**
- F12 → Console tab
- Look for JavaScript errors
- Check Network tab for failed requests

### Database Performance Slow

**Symptom:** Pages load very slowly (>5 seconds)

**Solutions:**

1. **Enable Query Logging**
```bash
# In .env, add:
SQLALCHEMY_ECHO=true

# Restart app, look for N+1 queries
```

2. **Check Database Size**
```sql
-- Connect to database
psql -h 127.0.0.1 -p 5439 -U postgres -d flask_dev

-- Check table sizes
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

3. **Add Indexes**
```sql
-- Example: Add index on frequently queried column
CREATE INDEX idx_applications_name ON applications(name);
CREATE INDEX idx_applications_status ON applications(lifecycle_status);
```

---

## STEP 9: ADVANCED CONFIGURATION

### Enable Redis Caching (Recommended for Production)

```bash
# Install Redis
# Windows: https://github.com/microsoftarchive/redis/releases
# Linux: sudo apt install redis-server
# Mac: brew install redis

# Start Redis
redis-server

# In .env, enable caching:
ENABLE_REDIS_CACHE=true
REDIS_URL=redis://localhost:6379/0

# Restart application
```

### Enable Celery for Async Tasks

```bash
# For batch imports and long-running operations

# In .env:
CELERY_ENABLED=true
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# Start Celery worker (separate terminal):
celery -A app.celery worker --loglevel=info

# Restart application
```

### Configure Email Notifications

```bash
# Option 1: MailHog (Local Testing)
# Download: https://github.com/mailhog/MailHog
# Run MailHog, access: http://localhost:8025

# In .env:
MAIL_SERVER=localhost
MAIL_PORT=1025
MAIL_USE_TLS=false

# Option 2: Production SMTP (e.g., SendGrid)
MAIL_SERVER=smtp.sendgrid.net
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=apikey
MAIL_PASSWORD=your-sendgrid-api-key
MAIL_DEFAULT_SENDER=noreply@yourcompany.com
```

### Set Up Monitoring (Production)

```bash
# Prometheus metrics available at:
# http://localhost:5000/metrics

# Grafana dashboards in:
# docs/monitoring/grafana_dashboard.json

# Error tracking:
# - Sign up for Sentry: https://sentry.io
# - Add to .env:
# SENTRY_DSN=your-sentry-dsn-here
```

---

## STEP 10: NEXT STEPS

### For Architects

1. **Import Your Portfolio**
   - Prepare Excel/CSV with applications
   - Use Batch Import feature
   - Map to capabilities
   - Run analysis

2. **Set Up Governance**
   - Define architectural principles
   - Create decision records (ADRs)
   - Set up approval workflows
   - Configure notifications

3. **Integrate with Existing Tools**
   - JIRA (for issues/tasks)
   - Abacus (for model sync)
   - Confluence (for documentation)
   - ServiceNow (for CMDB)

4. **Train Your Team**
   - Share this guide
   - Conduct walkthrough session
   - Create team-specific documentation
   - Set up office hours for questions

### For Developers

1. **Review Architecture**
   - Read `docs/` folder
   - Study model relationships
   - Understand service layer
   - Review API endpoints

2. **Run Tests**
```bash
# Run full test suite
pytest -v

# Run with coverage
pytest --cov=app --cov-report=html

# View coverage report
open htmlcov/index.html  # Mac/Linux
start htmlcov\index.html  # Windows
```

3. **Contribute Code**
   - Read CONTRIBUTING.md (if exists)
   - Follow code standards
   - Write tests for new features
   - Submit pull requests

### For Administrators

1. **Security Hardening**
   - Read `SECURITY_REMEDIATION.md`
   - Rotate API keys
   - Set up secrets management
   - Configure HTTPS
   - Enable rate limiting

2. **Backup Strategy**
```bash
# Database backup
pg_dump -h 127.0.0.1 -p 5439 -U postgres flask_dev > backup_$(date +%Y%m%d).sql

# Restore
psql -h 127.0.0.1 -p 5439 -U postgres flask_dev < backup_20260219.sql

# Automate with cron/Task Scheduler
```

3. **Monitoring Setup**
   - Error tracking (Sentry)
   - Performance monitoring (New Relic/Datadog)
   - Uptime monitoring (Pingdom/UptimeRobot)
   - Log aggregation (ELK stack)

---

## REFERENCE

### Key Files

| File | Purpose |
|------|---------|
| `.env` | Environment configuration (DO NOT commit!) |
| `config.py` | Flask configuration classes |
| `manage.py` | CLI commands for database/app management |
| `requirements.txt` | Python dependencies |
| `README.md` | Platform overview and features |
| `KNOWN_ISSUES.md` | Current bugs and limitations |
| `SECURITY_REMEDIATION.md` | Security best practices |

### Key Directories

| Directory | Contains |
|-----------|----------|
| `app/` | Application code |
| `app/models/` | Database models (162 files) |
| `app/services/` | Business logic (424 files) |
| `app/templates/` | HTML templates |
| `app/static/` | CSS, JavaScript, images |
| `migrations/` | Database migrations |
| `tests/` | Test suite |
| `docs/` | Documentation |

### Useful Commands

```bash
# Database
flask db current                     # Show current migration
flask db upgrade                     # Apply migrations
flask db history                     # Show migration history
python manage.py recreate_db         # Rebuild database (WARNING: destroys data)
python manage.py setup_dev           # Create admin user and roles

# Application
python manage.py runserver           # Start development server
python manage.py shell               # Python shell with app context
python manage.py routes              # List all routes

# Testing
pytest -v                            # Run tests verbose
pytest --cov=app                     # Run with coverage
pytest -k "test_login"               # Run specific tests

# Capabilities
flask acm seed                       # Seed ACM capabilities
flask acm domains                    # List capability domains
flask acm stats                      # Show coverage statistics
flask acm gaps                       # Analyze gaps

# Utilities
flask --help                         # Show all CLI commands
```

### API Documentation

**Swagger UI:** http://localhost:5000/apidocs/

**Key Endpoints:**
- `GET /api/applications` - List applications
- `POST /api/applications` - Create application
- `GET /api/applications/{id}` - Get application details
- `PUT /api/applications/{id}` - Update application
- `DELETE /api/applications/{id}` - Delete application
- `GET /api/vendors` - List vendors
- `GET /api/capabilities` - List capabilities
- `POST /api/ai-chat/message` - Send AI chat message

### Environment Variables

**Required:**
- `SECRET_KEY` - Flask session encryption (auto-generated if missing)
- `DATABASE_URL` - PostgreSQL connection string

**Optional:**
- `OPENAI_API_KEY` - For AI chat features
- `ANTHROPIC_API_KEY` - For Claude AI
- `REDIS_URL` - For caching
- `CELERY_ENABLED` - Enable async tasks
- `MAIL_SERVER` - Email notifications

**Feature Flags:**
- `USE_ARCHITECTURE_GUARDRAILS=true` - Enable v2 architecture module
- `USE_APPLICATIONS_GUARDRAILS=true` - Enable v2 applications module
- `USE_VENDORS_GUARDRAILS=true` - Enable v2 vendors module
- (See `.env` for all 11 v2 feature flags)

---

## GETTING HELP

### Documentation
- **Platform Docs:** `README.md`
- **API Docs:** http://localhost:5000/apidocs/
- **Known Issues:** `KNOWN_ISSUES.md`
- **Security:** `SECURITY_REMEDIATION.md`
- **Deployment:** `DEPLOYMENT_READINESS_GOVERNANCE.md`

### Support Channels
- **Internal Wiki:** [Link to your wiki]
- **Slack Channel:** #archie-support
- **Email:** archie-support@yourcompany.com
- **Office Hours:** Fridays 2-4 PM

### Reporting Bugs
```markdown
## Bug Report Template

**Title:** [Short description]

**Environment:**
- URL: http://localhost:5000
- Browser: Chrome 120
- User: admin@example.com
- Date: 2026-02-19

**Steps to Reproduce:**
1. Go to...
2. Click on...
3. Enter...
4. See error

**Expected:** [What should happen]
**Actual:** [What actually happened]

**Screenshots:** [If applicable]

**Error Message:**
```
[Paste error from console or logs]
```

**Priority:** Critical / High / Medium / Low
```

---

## SUCCESS CHECKLIST

After completing this guide, you should be able to:

- [✅] Start the application successfully
- [✅] Log in with admin credentials
- [✅] Create a new application
- [✅] View and edit applications
- [✅] Browse vendors and capabilities
- [✅] Use AI chat assistant
- [✅] Generate a report
- [✅] Understand database structure
- [✅] Troubleshoot common issues
- [✅] Know where to get help

**Congratulations! You're ready to use Entelim for enterprise architecture management.**

---

## APPENDIX: COMMON SCENARIOS

### Scenario 1: Import Existing Application Portfolio

```bash
# Prepare Excel file with columns:
# - Application Name
# - Description
# - Type
# - Status
# - Owner
# - Technology Stack

# Via Web UI:
1. Go to http://localhost:5000/batch-import
2. Upload Excel file
3. Map columns
4. Validate data
5. Click "Import"
6. Review results

# This creates applications and optionally:
# - Maps to capabilities
# - Links to vendors
# - Assigns owners
# - Sets lifecycle status
```

### Scenario 2: Conduct Vendor Risk Assessment

```bash
# Via Web UI:
1. Go to Vendors → Vendor Analysis
2. Select vendor
3. Click "Risk Assessment"
4. System analyzes:
   - Contract expiry dates
   - Vendor concentration (% apps on single vendor)
   - Compliance status
   - Financial health
   - Market position
5. View risk score and recommendations
6. Export report
```

### Scenario 3: Plan Technology Refresh

```bash
# Via Web UI:
1. Go to Strategic → Roadmaps
2. Create new roadmap: "Technology Refresh 2026"
3. Add initiatives:
   - "Migrate from Oracle to PostgreSQL"
   - "Cloud migration Phase 2"
   - "API standardization"
4. Link affected applications
5. Set timelines and budgets
6. Track progress
7. Generate executive dashboard
```

---

**Document Version:** 1.0  
**Last Updated:** 2026-02-19  
**Maintained By:** Entelim Development Team  
**Next Review:** 2026-03-01
