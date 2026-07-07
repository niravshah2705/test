# AuditEventService

```ts
interface AuditEventService {
  record(event: RecordAuditEventRequest): Promise<AuditEvent>;
  publish(event: DomainEvent): Promise<void>;
  listBySubject(subject: AuditSubject): Promise<AuditEvent[]>;
}
```

## Contracts

```ts
interface RecordAuditEventRequest {
  actorUserId?: UserId;
  subject: AuditSubject;
  action: string;
  metadata?: Record<string, unknown>;
}

interface AuditSubject {
  type: 'user' | 'traveler-profile' | 'flight-booking' | 'taxi-booking' | 'payment' | 'notification';
  id: string;
}

interface AuditEvent extends RecordAuditEventRequest {
  id: AuditEventId;
  occurredAt: string;
}

interface DomainEvent {
  name: string;
  occurredAt: string;
  payload: Record<string, unknown>;
}
```
