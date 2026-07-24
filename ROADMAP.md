# Scoring Engine Project Roadmap

This document outlines the development direction for the Scoring Engine project, organized by priority and scope.

## Project Vision

Scoring Engine aims to be the premier open-source platform for Red/White/Blue team cybersecurity competitions, providing:
- Reliable, automated service availability checking
- Intuitive interfaces for competition management and scoring
- Scalable architecture for competitions of any size
- Easy deployment in any environment, including airgapped networks

## Tracking Progress

- **This document**: High-level vision and phases (available in airgapped environments)
- **GitHub Project**: Active progress tracking at [Scoring Engine Roadmap](https://github.com/orgs/scoringengine/projects/)
- **GitHub Issues**: Individual tasks linked to roadmap items

## Current State (v2.1.0)

The project is **production-ready** with:
- 28 service check types covering common network services (auto-discovered from `scoring_engine/checks/`, including the red team agent check)
- Distributed task execution via Celery workers
- Web interface for scoreboard and team management
- Alembic-based database migrations (`bin/migrate`)
- Real-time updates over Server-Sent Events (`scoring_engine/sse_server.py`)
- Competition announcement feed with per-team/role audience targeting
- Inject workflow with templates, scored rubrics, and file attachments
- White team admin tooling: check dry-run and round rollback
- Multi-architecture Docker support (AMD64, ARM64)
- Comprehensive airgapped deployment support
- CI/CD pipeline with automated testing and image publishing

> Checkbox state below is reconciled against the code, not against intent. An item
> is only checked when the shipped implementation can be pointed at.

---

## Phase 1: Foundation Improvements

### Database & Data Management

- [x] **Implement database migrations with Alembic**
  - Alembic wired to the Flask app context (`alembic/env.py`), versions in `alembic/versions/`
  - `bin/migrate` runs pending migrations; `bin/migrate --check` exits 1 when any are pending
  - Fresh installs `create_all()` then stamp head; existing databases upgrade in place
  - Helpers in `scoring_engine/db.py`: `init_db()`, `run_migrations()`, `stamp_alembic_head()`
  - Workflow documented in `CLAUDE.md`
  - Note: implemented with Alembic directly, not Flask-Migrate

- [x] **Round rollback for competition data**
  - `POST /api/admin/rollback` deletes rounds, checks, and KB task manifests from a round onwards
  - Dry-run preview of what would be deleted, plus an explicit `confirm` flag on the destructive call
  - White team only, driven from the admin settings page

- [ ] **Optimize database query performance**
  - Address remaining N+1 query issues in API endpoints
  - Implement query result caching for expensive operations
  - Add database query logging for performance monitoring
  - Review and optimize team ranking calculations

### Code Quality & Testing

- [ ] **Increase test coverage to 90%+**
  - Add missing unit tests for web views
  - Expand model test coverage
  - Add API endpoint integration tests
  - Document testing best practices

- [ ] **Clean up technical debt**
  - Remove development hacks in agent API caching
  - Fix type handling for JavaScript select values in admin API
  - Implement proper validation for inject comment length (25K limit)
  - Resolve HTML embedding in API responses

### Documentation

- [ ] **Create operations guide**
  - Performance tuning recommendations
  - Backup and disaster recovery procedures
  - Troubleshooting common issues
  - Monitoring and alerting setup

- [ ] **Improve check documentation**
  - Document all 28 check types with examples (20 of 28 have a page under `docs/source/checks/`)
  - Provide competition.yaml templates for common scenarios
  - Create check development tutorial

- [ ] **Update README with version compatibility** ([#807](https://github.com/scoringengine/scoringengine/issues/807))
  - Document Python version requirements
  - Add dependency version matrix

### Setup & Onboarding

- [ ] **Integrate injects into setup process** ([#779](https://github.com/scoringengine/scoringengine/issues/779))
  - Allow inject configuration during bin/setup
  - Streamline initial competition setup

---

## Phase 2: Infrastructure Modernization

### Observability & Monitoring

- [ ] **Add metrics infrastructure**
  - Integrate Prometheus metrics endpoint
  - Export Celery worker metrics
  - Track check execution times and success rates
  - Monitor database and Redis performance

- [ ] **Implement structured logging**
  - Standardize log format across all components
  - Add correlation IDs for request tracing
  - Support log aggregation (ELK, Loki)
  - Create log analysis dashboards

- [ ] **Add health check endpoints**
  - Comprehensive health status for all services
  - Dependency health verification
  - Ready/live probes for container orchestration

### Kubernetes Support

- [ ] **Create Kubernetes deployment manifests**
  - Deployment specs for all components
  - ConfigMaps and Secrets management
  - Persistent volume claims for data
  - Network policies for security

- [ ] **Develop Helm chart**
  - Parameterized deployment configuration
  - Support for different cluster sizes
  - Include RBAC configuration
  - Document values and customization options

- [ ] **Horizontal Pod Autoscaling**
  - Worker autoscaling based on queue depth
  - Web tier autoscaling based on load
  - Resource requests and limits tuning

### API Improvements

- [ ] **Formalize REST API design**
  - Document API with OpenAPI/Swagger specification
  - Standardize error response format
  - Version API endpoints
  - Remove HTML from API responses

- [ ] **Add API authentication options**
  - API key authentication for integrations
  - JWT token support
  - Rate limiting per client
  - Audit logging for API access

---

## Phase 3: Feature Enhancements

### Competition Management

- [x] **Enhance inject system**
  - Rubric updates: `RubricItem` / `InjectRubricScore` in `scoring_engine/models/inject.py`, edited in place via `PUT /api/admin/injects/templates/<id>` so existing scores survive
  - Inject generation from templates: `Template` model plus create/update/delete/import endpoints that fan a template out to selected teams
  - File attachments: `InjectFile` model with upload, list, download, preview, and delete endpoints
  - Still open: inject workflow documentation (nothing under `docs/` covers it yet)

- [x] **Service check dry-run**
  - `POST /api/admin/check/dry_run` runs a check through a Celery worker without recording a result
  - Lets white team validate service configuration before a round scores it

- [ ] **Incident response reporting** ([#781](https://github.com/scoringengine/scoringengine/issues/781))
  - Enable submission of incident response reports
  - Grading and feedback workflow
  - Similar interface to existing injects component

- [ ] **Improve team management**
  - Batch operations for team administration
  - Team import/export functionality
  - Enhanced permission controls
  - Team notification system improvements

- [ ] **Add competition templates**
  - Pre-built competition.yaml templates
  - Common service configurations
  - Difficulty presets
  - Quick-start competition wizard

- [ ] **Flag capture admin interface** ([#778](https://github.com/scoringengine/scoringengine/issues/778))
  - Administrative view for flag scoring
  - Flag capture status overview
  - Bulk flag management

### Team Communication

- [ ] **Authenticated team wiki** ([#782](https://github.com/scoringengine/scoringengine/issues/782))
  - Knowledge base accessible to authenticated teams
  - Team-specific info, credentials, network details
  - White team editable (HTML/Markdown support)
  - Not started. `scoring_engine/models/kb.py` is unrelated despite the name — it is an
    engine-internal per-round Celery task manifest (`name="task_ids"`) used only by
    `engine/engine.py` and the admin rollback endpoint. There is no wiki view, template, or API.

- [x] **Competition announcement feed** ([#783](https://github.com/scoringengine/scoringengine/issues/783))
  - `Announcement` model with audience targeting (`global`, `all_blue`, `all_red`, `team:<id>`, `teams:1,3,5`), pinning, activation, and expiry
  - `/api/announcements` and `/api/announcements/count` for teams; full admin CRUD under `/api/admin/announcements`
  - Team-facing `announcements.html` page and an admin management page
  - Unread badge in the navbar refreshes on the `round_complete` SSE event, with polling fallback
  - Known gap: no `announcement` event is published when white team posts one, so a new
    announcement is not pushed out until the next round completes or the poll fires

### New Check Types

- [ ] **Elasticsearch authentication support** ([#738](https://github.com/scoringengine/scoringengine/issues/738))
  - Add optional authentication to Elasticsearch check
  - Support modern Elasticsearch security features

- [ ] **Cloud service checks**
  - AWS service health (Lambda, RDS, S3)
  - Azure service checks
  - GCP service verification
  - Generic cloud API health checks

- [ ] **Expanded browser automation**
  - Additional Playwright-based checks
  - Multi-step workflow verification
  - Visual regression testing
  - JavaScript application checks

- [ ] **Advanced protocol checks**
  - WebSocket connectivity and messaging
  - gRPC service verification
  - GraphQL endpoint testing
  - Message queue checks (RabbitMQ, Kafka)

### User Experience

- [x] **Real-time updates**
  - Implemented with Server-Sent Events rather than WebSockets
  - `scoring_engine/events.py` publishes to a Redis pub/sub channel from the engine and web tiers
  - `scoring_engine/sse_server.py` is a gevent SSE server on port 8001 that filters events per client role/team
  - `static/vendor/js/sse.js` is an `EventSource` client with polling fallback, loaded from `base.html`
  - Events published today: `round_complete`, `inject_update`, `settings_changed`

- [ ] **Service uptime dashboard** ([#804](https://github.com/scoringengine/scoringengine/issues/804))
  - Monitoring page showing uptime percentage per host
  - Service availability tracking
  - Historical uptime data

- [ ] **Scoreboard display modes** ([#780](https://github.com/scoringengine/scoringengine/issues/780))
  - Admin-configurable display options
  - All Green, All Red, Random modes
  - Customizable scoreboard presentation

- [ ] **Separate BTA uptime page** ([#786](https://github.com/scoringengine/scoringengine/issues/786))
  - Dedicated page for Business Transaction Analysis checks
  - Separate from functional scored services
  - Focused uptime monitoring view

- [ ] **Enhanced visualizations**
  - Historical score graphs
  - Service uptime timelines
  - Team comparison views
  - Competition statistics dashboard

---

## Phase 4: Scalability & Enterprise Features

### Cloud-Native Deployment

- [ ] **Cloud provider templates**
  - AWS CloudFormation/CDK templates
  - Azure ARM/Bicep templates
  - GCP Deployment Manager configs
  - Terraform modules for multi-cloud

- [ ] **Managed service integration**
  - Support for managed MySQL/PostgreSQL
  - Managed Redis/ElastiCache
  - Cloud-native secret management
  - Object storage for artifacts

### Advanced Scaling

- [ ] **Multi-region support**
  - Distributed competition instances
  - Cross-region check execution
  - Data synchronization strategies
  - Latency-aware routing

- [ ] **Large-scale optimizations**
  - Support for 100+ teams
  - Thousands of concurrent checks
  - Optimized caching strategies
  - Database sharding considerations

### Security Enhancements

- [ ] **Advanced RBAC**
  - Granular permission system
  - Custom role definitions
  - Permission inheritance
  - Scope-based access control

- [ ] **Audit logging**
  - Comprehensive action logging
  - Tamper-evident audit trail
  - Log retention policies
  - Compliance reporting

- [ ] **Security hardening**
  - Security benchmark compliance
  - Automated vulnerability scanning
  - Secrets rotation support
  - Network segmentation guidance

---

## Future Considerations

These items are under consideration for future development:

### Modern Frontend
- Evaluate React/Vue.js for improved user experience
- Progressive Web App (PWA) capabilities
- Mobile-responsive design improvements
- Offline mode for disconnected scenarios

### Plugin Architecture
- Formalized check plugin system
- Third-party check marketplace
- Custom scoring algorithms
- Event hook system for extensions

### Advanced Analytics
- Machine learning for anomaly detection
- Predictive scoring models
- Competition difficulty analysis
- Automated report generation

### Integration Ecosystem
- CTFd integration
- Pwnboard integration ([#785](https://github.com/scoringengine/scoringengine/issues/785))
- Learning management system (LMS) connectors
- Slack/Discord notifications
- Ticketing system integration

---

## Contributing to the Roadmap

We welcome community input on project direction:

1. **Feature Requests**: Open a GitHub issue with the `enhancement` label
2. **Discussions**: Use GitHub Discussions for roadmap conversations
3. **Pull Requests**: Contributions toward roadmap items are encouraged

### Prioritization Criteria

Items are prioritized based on:
- Community demand and feedback
- Impact on competition operators
- Technical dependencies
- Maintainability and sustainability

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.2 | 2026-07-24 | Reconciled against the code: checked Alembic migrations, round rollback, inject enhancements, check dry-run, announcement feed, and real-time updates; corrected check count 31 → 28 and Current State to v2.1.0; confirmed the team wiki (#782) is still unstarted |
| 1.1 | 2025-01-20 | Added community-requested features from GitHub issues |
| 1.0 | 2025-01-20 | Initial roadmap creation |

---

*This roadmap is a living document and will be updated as the project evolves.*
