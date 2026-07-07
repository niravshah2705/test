# TravelerProfileService

```ts
interface TravelerProfileService {
  createProfile(request: CreateTravelerProfileRequest, actor: AuthContext): Promise<TravelerProfile>;
  updateProfile(profileId: TravelerProfileId, request: UpdateTravelerProfileRequest, actor: AuthContext): Promise<TravelerProfile>;
  getProfile(profileId: TravelerProfileId, actor: AuthContext): Promise<TravelerProfile>;
  listProfiles(userId: UserId, actor: AuthContext): Promise<TravelerProfile[]>;
}
```

## Contracts

```ts
interface TravelerProfile {
  id: TravelerProfileId;
  userId: UserId;
  fullName: string;
  dateOfBirth?: string;
  contact: ContactPoint;
  address?: PostalAddress;
  loyaltyPrograms: LoyaltyProgram[];
}

interface LoyaltyProgram {
  carrierCode: string;
  memberNumber: string;
}

interface CreateTravelerProfileRequest extends Omit<TravelerProfile, 'id'> {}
interface UpdateTravelerProfileRequest extends Partial<Omit<TravelerProfile, 'id' | 'userId'>> {}
```
