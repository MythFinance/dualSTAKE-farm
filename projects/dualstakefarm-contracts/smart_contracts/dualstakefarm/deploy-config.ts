import * as algokit from '@algorandfoundation/algokit-utils'
import { DualstakeFarmFactory } from '../artifacts/dualstakefarm/DualstakeFarmClient'
import algosdk from 'algosdk'

// Below is a showcase of various deployment options you can use in TypeScript Client
export async function deploy() {
  algokit.Config.configure({
    debug: true,
    populateAppCallResources: true,
    // traceAll: true,
  })

  console.log('=== Deploying Dualstakefarm ===')

  const algorand = algokit.AlgorandClient.fromEnvironment()
  const deployer = await algorand.account.fromEnvironment('DEPLOYER')

  const factory = algorand.client.getTypedAppFactory(DualstakeFarmFactory, {
    defaultSender: deployer.addr,
  })

  const { appClient, result } = await factory.deploy({ onUpdate: 'append', onSchemaBreak: 'append' })

  // If app was just created fund the app account
  if (['create', 'replace'].includes(result.operationPerformed)) {
    await algorand.send.payment({
      amount: (0.1).algo(),
      sender: deployer.addr,
      receiver: appClient.appAddress,
    })
  }

  console.log("Dualstakefarm app id "+result.appId)
}
