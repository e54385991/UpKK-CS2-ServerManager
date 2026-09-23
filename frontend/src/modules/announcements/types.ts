export type Announcement = {
  readonly id: number;
  readonly title: string;
  readonly bodyMarkdown: string;
  readonly isPublished: boolean;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly publishedAt: string | null;
};

export type AnnouncementWrite = Pick<
  Announcement,
  "title" | "bodyMarkdown" | "isPublished"
>;

export type CS2UpdateNotice = {
  readonly version: string;
  readonly changedAt: string;
  readonly expiresAt: string;
};

export type PublishedAnnouncementFeed = {
  readonly items: Announcement[];
  readonly cs2UpdateNotice: CS2UpdateNotice | null;
};
