# NotificationService

```ts
interface NotificationService {
  send(request: SendNotificationRequest): Promise<NotificationReceipt>;
  getStatus(notificationId: NotificationId): Promise<NotificationReceipt>;
}
```

## Contracts

```ts
interface SendNotificationRequest {
  recipient: ContactPoint;
  channel: 'email' | 'sms' | 'push';
  templateKey: string;
  data: Record<string, unknown>;
}

interface NotificationReceipt {
  id: NotificationId;
  status: 'queued' | 'sent' | 'delivered' | 'failed';
  providerReference?: string;
}
```
