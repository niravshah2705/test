# IdentityService

```ts
interface IdentityService {
  authenticate(request: AuthenticateRequest): Promise<AuthSession>;
  validateSession(token: string): Promise<AuthContext>;
  revokeSession(sessionId: string, actor: AuthContext): Promise<void>;
  getAuthContext(userId: UserId): Promise<AuthContext>;
}
```

## Contracts

```ts
interface AuthenticateRequest {
  strategy: 'password' | 'oauth' | 'magic-link';
  subject: string;
  secret?: string;
}

interface AuthSession {
  sessionId: string;
  userId: UserId;
  accessToken: string;
  expiresAt: string;
}

interface AuthContext {
  userId: UserId;
  roles: string[];
  permissions: string[];
}
```
