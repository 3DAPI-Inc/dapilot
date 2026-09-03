import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';
import { writeFileSync } from 'fs';
import { AppModule } from '/Users/sofiamanu/Desktop/work/3dapi/public-api/v1/src/app.module';

async function main() {
  const app = await NestFactory.create(AppModule, { logger: false, abortOnError: false });
  app.setGlobalPrefix('v1');
  const cfg = new DocumentBuilder()
    .setTitle('dapi Public API v1')
    .setVersion('1.0')
    .addApiKey({ type: 'apiKey', in: 'header', name: 'x-api-key' }, 'api_key')
    .build();
  const doc = SwaggerModule.createDocument(app, cfg);
  writeFileSync(process.argv[2], JSON.stringify(doc, null, 2));
  console.log('paths:', Object.keys(doc.paths).length, 'schemas:', Object.keys(doc.components?.schemas ?? {}).length);
  await app.close().catch(() => {});
  process.exit(0);
}
main().catch((e) => { console.error('FAILED:', e?.message ?? e); process.exit(1); });
