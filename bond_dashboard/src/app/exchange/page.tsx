import { PageHeader } from "@/components/page-header";
import { MessageBoard } from "@/components/message-board";

export const metadata = { title: "信息交流栏 · 债用债一级投资看板" };

export default function ExchangePage() {
  return (
    <>
      <PageHeader
        title="信息交流栏"
        description="团队内部信息分享与交流（数据保存在本机）"
      />
      <MessageBoard />
    </>
  );
}
