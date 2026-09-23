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
