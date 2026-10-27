-- CityLens 本地演示专用合成笔记。仅在隔离的开发数据库中使用；不是实际用户评价。
-- 原始初始化文件的部分笔记关联了错误店铺，先把它们改成明确标注的演示资料。
SET NAMES utf8mb4;
UPDATE tb_blog SET title='【演示笔记】Mamala 的约会氛围参考', images='', liked=0, comments=0,
  content='演示数据，非真实用户评价。Mamala(杭州远洋乐堤港店)的店铺资料可作为约会场景候选；环境体验请到店核实。'
  WHERE id=4 AND shop_id=4;
UPDATE tb_blog SET title='【演示笔记】103茶餐厅用餐参考', images='', liked=0, comments=0,
  content='演示数据，非真实用户评价。103茶餐厅适合茶餐厅类别检索；菜品、排队和安静程度请到店核实。'
  WHERE id=5 AND shop_id=1;
UPDATE tb_blog SET title='【演示笔记】开乐迪KTV 时间安排', images='', liked=0, comments=0,
  content='演示数据，非真实用户评价。开乐迪KTV（运河上街店）可作为饭后唱歌候选；营业时间和包厢状态请到店核实。'
  WHERE id=6 AND shop_id=10;
UPDATE tb_blog SET title='【演示笔记】开乐迪KTV 团体活动', images='', liked=0, comments=0,
  content='演示数据，非真实用户评价。开乐迪KTV（运河上街店）可用于演示两站路线；价格仅以店铺人均价估算。'
  WHERE id=7 AND shop_id=10;

INSERT INTO tb_blog (id, shop_id, user_id, title, images, content, liked, comments)
VALUES
 (1001, 2, 1, '【演示笔记】蔡馬洪涛烤肉用餐参考', '', '演示数据，非真实用户评价。蔡馬洪涛烤肉·老北京铜锅涮羊肉可作为烤肉和聚餐候选；实际体验请到店核实。', 0, 0),
 (1002, 3, 1, '【演示笔记】新白鹿餐厅聚餐参考', '', '演示数据，非真实用户评价。新白鹿餐厅(运河上街店)可作为朋友聚餐候选；排队情况请到店核实。', 0, 0),
 (1003, 5, 1, '【演示笔记】海底捞火锅晚餐参考', '', '演示数据，非真实用户评价。海底捞火锅(水晶城购物中心店)可作为晚餐候选；实际营业和等位情况请到店核实。', 0, 0),
 (1004, 8, 1, '【演示笔记】浅草屋寿司约会参考', '', '演示数据，非真实用户评价。浅草屋寿司（运河上街店）可作为约会候选；安静程度请到店核实。', 0, 0),
 (1005, 11, 1, '【演示笔记】INLOVE KTV 饭后安排', '', '演示数据，非真实用户评价。INLOVE KTV(水晶城店)可作为饭后唱歌候选；实际包厢和价格请到店核实。', 0, 0),
 (1006, 12, 1, '【演示笔记】魅 KTV 夜间安排', '', '演示数据，非真实用户评价。魅(杭州远洋乐堤港店)可作为夜间活动候选；实际营业时间请到店核实。', 0, 0),
 (1007, 13, 1, '【演示笔记】讴K拉量贩KTV 团体活动', '', '演示数据，非真实用户评价。讴K拉量贩KTV(北城天地店)可作为团体唱歌候选；实际包厢请到店核实。', 0, 0),
 (1008, 14, 1, '【演示笔记】星聚会KTV 两站路线', '', '演示数据，非真实用户评价。星聚会KTV(拱墅区万达店)可用于两站规划；交通时间需另行确认。', 0, 0)
ON DUPLICATE KEY UPDATE title=VALUES(title), images=VALUES(images), content=VALUES(content);

-- CityLens 写操作演示券。仅用于隔离开发库；再次导入时不重置已领取订单和库存。
INSERT IGNORE INTO tb_voucher (id, shop_id, title, sub_title, rules, pay_value, actual_value, type, status)
VALUES
 (1001, 1, '【演示】5元免费体验券', 'CityLens 免费领取流程', '演示数据；仅供本地流程验证，不代表真实商家承诺。', 0, 500, 0, 1),
 (1002, 1, '【演示】20元限时券', 'CityLens 秒杀下单流程', '演示数据；创建待支付订单，不含支付功能。', 990, 2000, 1, 1);
INSERT IGNORE INTO tb_seckill_voucher (voucher_id, stock, begin_time, end_time)
VALUES (1002, 20, '2026-01-01 00:00:00', '2030-12-31 23:59:59');
