# ADR 0002: Modular Monolith Architecture

## Status
Accepted (pending tech lead approval)

## Context
This document justifies the choice of a modular monolith architecture over microservices for the MVP scope of the travel booking platform.

### Business Context
- **MVP Scope**: Core functionality to book flights, manage traveler profiles, process payments, and handle notifications
- **Timeline**: Rapid iteration with 3-6 month MVP horizon
- **Team Size**: Small engineering team (5-10 engineers)
- **Business Goals**: Validate product-market fit, establish core workflows, generate early revenue

### Technical Constraints
- Limited infrastructure resources for MVP
- Need for rapid development velocity
- Requirement for strong consistency across domain boundaries
- Simplicity in deployment and monitoring

## Decision
Adopt a **modular monolith** architecture where:

1. **Single Deployment Unit**: One executable/deployable artifact containing all business logic
2. **Explicit Module Boundaries**: Clear separation of concerns with defined module interfaces
3. **Domain-Driven Design**: Modules organized around core business capabilities (flight booking, payments, traveler profiles, notifications)
4. **Internal Module Communication**: In-process method calls between modules (no network overhead)

### Architecture Layers

```
┌─────────────────────────────────────────────────────────────┐
│                    Presentation Layer                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  API     │  │  Admin   │  │  Web UI  │  │  CLI     │   │
│  │ Endpoints│  │ Panel    │  │          │  │          │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│              Application / Composition Layer                 │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Use Cases orchestrating domain modules              │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                  Domain / Module Layer                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Flight       │  │ Payments     │  │ Traveler     │     │
│  │ Booking      │  │              │  │ Profiles     │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Notifications│  │ Search       │  │ Provider     │     │
│  │              │  │              │  │ Adapters     │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                  Infrastructure Layer                        │
│  Database │ Cache │ Message Queue │ External APIs           │
└─────────────────────────────────────────────────────────────┘
```

### Module Boundaries

Each module encapsulates:
- **Entities**: Core domain objects with identity and lifecycle
- **Value Objects**: Immutable, defined-by-attributes objects
- **Repositories**: Interface for data access (implementation in infrastructure layer)
- **Domain Services**: Business logic that doesn't belong to a single entity
- **Application Services**: Use case orchestration (external interface to module)

Modules communicate via:
- **In-process method calls** (preferred, zero latency)
- **Domain events** (asynchronous cross-module notifications)
- **Shared kernel** for common value objects and interfaces

## Consequences

### Positive Consequences
1. **Simplicity**: Single deployment unit simplifies CI/CD, monitoring, and debugging
2. **Performance**: No network overhead for inter-module communication
3. **Strong Consistency**: ACID transactions across module boundaries
4. **Development Velocity**: Faster iteration without service coordination
5. **Cost Efficiency**: Lower infrastructure costs during MVP phase

### Trade-offs and Mitigations
1. **Scalability**: Single deployment unit scales as one
   - *Mitigation*: Use horizontal pod autoscaling; consider module extraction when traffic justifies it
   
2. **Team Scalability**: Multiple teams must coordinate on single codebase
   - *Mitigation*: Clear module boundaries, documented interfaces, feature flags for parallel development
   
3. **Technology Lock-in**: All modules share runtime and dependencies
   - *Mitigation*: Modular design allows future extraction of independent services when needed

4. **Failure Isolation**: Failure in one module affects entire application
   - *Mitigation*: Circuit breakers, bulkheads, graceful degradation patterns within monolith

## Scalability Assumptions

### MVP Phase (0-10K MAU)
- Single database instance with connection pooling
- Horizontal scaling via container replicas (2-4 instances)
- Redis cache for session and frequently accessed data
- Synchronous inter-module calls acceptable (<5ms latency)

### Growth Phase (10K-100K MAU)
- Database read replicas for query scaling
- Caching layer expansion with CDN for static assets
- Async processing via message queue for non-critical workflows
- Consider module extraction when:
  - Team size exceeds 20 engineers
  - Different scaling requirements per module
  - Different deployment frequency per capability

### Enterprise Phase (100K+ MAU)
- Potential extraction of high-volume modules (search, booking) to independent services
- Event-driven architecture for decoupled workflows
- Multi-region deployment with data locality requirements

## Deployment Strategy

### MVP: Single Container Deployment
```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: travel-platform
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: app
          image: travel-platform:latest
          ports:
            - containerPort: 8080
          resources:
            requests:
              memory: "512Mi"
              cpu: "500m"
```

### CI/CD Pipeline
1. **Build**: Single Docker build for entire application
2. **Test**: Module-scoped unit tests + integration tests
3. **Deploy**: Blue-green deployment to Kubernetes
4. **Monitor**: Centralized logging and metrics across all modules

### Environment Strategy
- **Development**: Local monolith with hot reload
- **Staging**: Single deployment matching production configuration
- **Production**: Multiple replicas with health checks and auto-scaling

## Alternatives Considered

### 1. Microservices Architecture
**Rationale for Rejection (MVP)**:
- Operational complexity: Service discovery, distributed tracing, circuit breakers
- Data consistency challenges: Sagas, event sourcing, eventual consistency
- Infrastructure overhead: Multiple deployments, service mesh requirements
- Team coordination: Cross-service changes require multiple PRs and deployments

**When to Reconsider**: When team size exceeds 20 engineers or when specific modules have fundamentally different scaling requirements.

### 2. Layered Architecture (Three-Tier)
**Rationale for Rejection**:
- Obscures domain boundaries and business ownership
- Encourages anemic domain models
- Makes it difficult to identify module-level responsibilities
- Harder to extract modules later when needed

### 3. Hexagonal/Ports and Adapters
**Rationale for Partial Adoption**:
- Adopted for external interface isolation (API, messaging)
- Internal modules still use direct method calls for simplicity
- Balance between structure and development velocity

## Implementation Checklist

### Phase 1: Foundation (Weeks 1-2)
- [ ] Define module boundaries and interfaces
- [ ] Set up shared types and value objects
- [ ] Implement repository abstraction layer
- [ ] Configure CI/CD pipeline for monolith

### Phase 2: Core Modules (Weeks 3-6)
- [ ] Flight booking module
- [ ] Traveler profiles module
- [ ] Payments module
- [ ] Notifications module

### Phase 3: Integration (Weeks 7-8)
- [ ] Cross-module workflows (booking → payment → notification)
- [ ] Domain event publishing/subscribing
- [ ] Error handling and retry mechanisms
- [ ] Monitoring and alerting setup

### Phase 4: Production Ready (Weeks 9-10)
- [ ] Performance testing and optimization
- [ ] Security audit and hardening
- [ ] Documentation completion
- [ ] Tech lead review and approval

## References
- [Domain-Driven Design](https://www.amazon.com/Domain-Driven-Design-Tackling-Complexity-Inside/dp/0321125215) - Eric Evans
- [Clean Architecture](https://www.amazon.com/Clean-Architecture-Craftsmans-Software-Structure/dp/0134494278) - Robert C. Martin
- [Monolith First](https://martinfowler.com/bliki/MonolithFirst.html) - Martin Fowler
- [The Modular Monolith](https://medium.com/swlh/the-modular-monolith-a-java-architecture-6e451c5f3b1a)

## Appendix: Module Dependency Graph

```
flight-booking
    ├── traveler-profiles (read)
    ├── payments (write)
    └── notifications (publish)

payments
    ├── traveler-profiles (read)
    └── notifications (publish)

traveler-profiles
    └── notifications (publish)

notifications
    └── (no internal dependencies)

flight-search
    └── flight-booking (read)
```

## History
- 2026-07-09: Initial draft - Modular monolith for MVP scope
- Pending: Tech lead review and approval
