-- MySQL 容器首次启动时自动执行
-- 目的：确保 shopping_guide 库存在、字符集正确、shopping_user 拥有读写权限
CREATE DATABASE IF NOT EXISTS shopping_guide
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'shopping_user'@'%' IDENTIFIED BY 'shopping_pwd';
GRANT ALL PRIVILEGES ON shopping_guide.* TO 'shopping_user'@'%';
FLUSH PRIVILEGES;
