import * as algokit from '@algorandfoundation/algokit-utils'
import algosdk, { makeEmptyTransactionSigner } from 'algosdk'
import {
  FarmStateFromTuple,
  FarmState as FarmStateInternal,
  DualstakeFarmClient,
  DualstakeFarmComposer,
} from './DualstakeFarmClient.js'

const TXN_VALIDITY = 8

export interface FarmState extends FarmStateInternal {
  appId: bigint
  appEscrow: string
}

export interface DSFarmSDKConstructor {
  algorand: algokit.AlgorandClient
  appId: bigint
  sender: string
}

export interface DSFarmCost {
  algoCost: bigint
  details: {
    optinCost: bigint
    boxCost: bigint
    farmCost: bigint
  }
}

export interface DSFarmParams extends DSFarmCost {
  maxDuration: bigint
}

const signer = makeEmptyTransactionSigner()

export class DSFarmSDK {
  client: DualstakeFarmClient
  algorand: algokit.AlgorandClient
  sender: string

  constructor({ appId, algorand, sender }: DSFarmSDKConstructor) {
    algokit.Config.configure({
      debug: false,
      populateAppCallResources: false,
      // traceAll: true,
    })

    this.algorand = algorand
    algorand.setSigner(sender, makeEmptyTransactionSigner())

    this.client = this.algorand.client.getTypedAppClientById(DualstakeFarmClient, {
      appId,
      defaultSender: sender,
      defaultSigner: signer,
    })

    this.sender = sender
  }

  get appId() {
    return this.client.appId
  }

  async getFarms(): Promise<Map<bigint, FarmState>> {
    const { algod } = this.client.algorand.client
    const { boxes } = await algod.getApplicationBoxes(Number(this.appId)).do()
    const appIds = boxes.map(({ name: n }: { name: Uint8Array }) => algosdk.decodeUint64(n, 'bigint'))

    const { confirmations } = await this.client
      .newGroup()
      .logStates({
        args: {
          boxNames: appIds,
        },
      })
      .simulate({
        allowMoreLogging: true,
        allowUnnamedResources: true,
        extraOpcodeBudget: 130013,
        fixSigners: true,
        allowEmptySignatures: true,
      })

    const farmStates = new Map<bigint, FarmState>()

    const logs = confirmations[0]!.logs ?? []
    for (let idx = 0; idx < logs.length; idx++) {
      const appId = appIds[idx]
      const appEscrow = algosdk.getApplicationAddress(appId)
      const method = this.client.appClient.getABIMethod('get_state')
      const farmState = FarmStateFromTuple(
        // @ts-ignore
        method.returns.type.decode(logs[idx]),
      )
      farmStates.set(appId, {
        ...farmState,
        appId,
        appEscrow,
      })
    }

    return farmStates
  }

  async getFarm(dsAppId: bigint): Promise<FarmState> {
    const { returns } = await this.client
      .newGroup()
      .getState({
        args: {
          recipientApp: dsAppId,
        },
        signer: makeEmptyTransactionSigner(),
      })
      .simulate({
        allowMoreLogging: true,
        allowUnnamedResources: true,
        extraOpcodeBudget: 700,
        fixSigners: true,
        allowEmptySignatures: true,
      })

    const internalState = returns[0]!

    return {
      ...internalState,
      appId: dsAppId,
      appEscrow: algosdk.getApplicationAddress(dsAppId),
    }
  }

  async getFarmParams({
    dsAppId: recipientApp,
    farmAssetId,
    durationBlocks,
  }: {
    dsAppId: bigint
    farmAssetId: bigint
    durationBlocks: bigint
  }): Promise<DSFarmParams> {
    const {
      returns: [algoCostStruct],
    } = await this.client
      .newGroup()
      .getAlgoCostAndMaxDuration({
        args: {
          farmAsset: farmAssetId,
          recipientApp,
          durationBlocks,
        },
        validityWindow: 125,
      })
      .simulate({
        allowUnnamedResources: true,
        fixSigners: true,
        allowEmptySignatures: true,
      })

    const { algoCost, optinCost, boxCost, farmCost, maxDuration } = algoCostStruct!
    return { algoCost: algoCost, maxDuration, details: { optinCost, boxCost, farmCost } }
  }

  async getFarmCreationCost({
    dsAppId: recipientApp,
    farmAssetId,
    durationBlocks,
  }: {
    dsAppId: bigint
    farmAssetId: bigint
    durationBlocks: bigint
  }): Promise<DSFarmCost> {
    const {
      returns: [algoCostStruct],
    } = await this.client
      .newGroup()
      .getAlgoCost({
        args: {
          farmAsset: farmAssetId,
          recipientApp,
          durationBlocks,
        },
      })
      .simulate({
        allowUnnamedResources: true,
        fixSigners: true,
        allowEmptySignatures: true,
      })

    const { algoCost, optinCost, boxCost, farmCost } = algoCostStruct!

    return { algoCost: algoCost!, details: { optinCost, boxCost, farmCost } }
  }

  async makeCreateFarmTransactions({
    dsAppId,
    durationBlocks,
    amountPerBlock,
    farmAssetId,
  }: {
    dsAppId: bigint
    durationBlocks: bigint
    amountPerBlock: bigint
    farmAssetId: bigint
  }): Promise<algosdk.EncodedTransaction[]> {
    const { algoCost, maxDuration } = await this.getFarmParams({
      dsAppId,
      farmAssetId,
      durationBlocks,
    })

    if (durationBlocks > maxDuration) {
      throw new Error(`Duration (${durationBlocks} blocks) exceeds current allowed duration (${maxDuration} blocks)`)
    }

    await this.algorand.createTransaction.payment({
      sender: this.sender,
      receiver: this.client.appAddress,
      amount: algoCost!.microAlgo(),
      signer,
    })

    const composer = await this.client
      .newGroup()
      .addTransaction(
        await this.algorand.createTransaction.payment({
          sender: this.sender,
          receiver: this.client.appAddress,
          amount: algoCost!.microAlgo(),
          signer,
        }),
        signer,
      )
      .createFarm({
        args: {
          durationBlocks,
          amountPerBlock,
          farmAsset: farmAssetId,
          recipientApp: dsAppId,
        },
        staticFee: (2000).microAlgos(),
        boxReferences: [algosdk.encodeUint64(dsAppId)],
        validityWindow: 125,
        signer,
      })
      .addTransaction(
        await this.algorand.createTransaction.assetTransfer({
          sender: this.sender,
          receiver: this.client.appAddress,
          assetId: farmAssetId,
          amount: durationBlocks * amountPerBlock,
          signer,
        }),
        signer,
      )
      .composer()

    const { transactions: ts } = await composer.buildTransactions()
    const transactions = algosdk.assignGroupID(
      ts.map((t) => algosdk.Transaction.from_obj_for_encoding(t.get_obj_for_encoding())),
    )
    return transactions.map((t) => t.get_obj_for_encoding())
  }

  async makeExtendFarmAmountTransactions({ dsAppId, amountPerBlock }: { dsAppId: bigint; amountPerBlock: bigint }) {
    const { farmAsset: farmAssetId, remainingDurationBlocks: durationBlocks } = await this.getFarm(dsAppId)

    const composer = await this.client
      .newGroup()
      .extendAmountPerBlock({
        args: {
          amountPerBlock,
          recipientApp: dsAppId,
        },
        staticFee: (2000).microAlgos(),
        boxReferences: [algosdk.encodeUint64(dsAppId)],
        sender: this.sender,
        signer,
      })
      .addTransaction(
        await this.algorand.createTransaction.assetTransfer({
          sender: this.sender,
          receiver: this.client.appAddress,
          assetId: farmAssetId,
          amount: durationBlocks * amountPerBlock,
          signer,
        }),
      )
      .composer()

    const { transactions: ts } = await composer.buildTransactions()
    const transactions = algosdk.assignGroupID(
      ts.map((t) => algosdk.Transaction.from_obj_for_encoding(t.get_obj_for_encoding())),
    )
    return transactions.map((t) => t.get_obj_for_encoding())
  }

  async makeExtendFarmDurationTransactions({
    dsAppId,
    extendDurationInBlocks,
  }: {
    dsAppId: bigint
    extendDurationInBlocks: bigint
  }) {
    const farmState = await this.getFarm(dsAppId)
    const { farmAsset: farmAssetId, remainingDurationBlocks } = farmState

    const { algoCost, maxDuration } = await this.getFarmParams({
      dsAppId,
      farmAssetId,
      durationBlocks: extendDurationInBlocks,
    })

    if (remainingDurationBlocks + extendDurationInBlocks > maxDuration) {
      throw new Error(
        `Duration (extending ${extendDurationInBlocks} blocks, existing ${remainingDurationBlocks}) exceeds current allowed duration (${maxDuration} blocks)`,
      )
    }

    const composer = await this.client
      .newGroup()
      .addTransaction(
        await this.algorand.createTransaction.payment({
          sender: this.sender,
          receiver: this.client.appAddress,
          amount: algoCost!.microAlgo(),
          signer,
        }),
      )
      .extendDurationBlocks({
        args: {
          durationBlocks: extendDurationInBlocks,
          recipientApp: dsAppId,
        },
        staticFee: (2000).microAlgos(),
        boxReferences: [algosdk.encodeUint64(dsAppId)],
        sender: this.sender,
        signer,
        validityWindow: 125,
      })
      .addTransaction(
        await this.algorand.createTransaction.assetTransfer({
          sender: this.sender,
          receiver: this.client.appAddress,
          assetId: farmAssetId,
          amount: extendDurationInBlocks * farmState.amountPerBlock,
          signer,
        }),
      )
      .composer()

    const { transactions: ts } = await composer.buildTransactions()
    const transactions = algosdk.assignGroupID(
      ts.map((t) => algosdk.Transaction.from_obj_for_encoding(t.get_obj_for_encoding())),
    )
    return transactions.map((t) => t.get_obj_for_encoding())
  }

  async makePayoutTransactions({
    listing,
    dsState,
    blockRound,
    lastRound,
    callSwap,

    txnValidity = TXN_VALIDITY,
  }: {
    listing: { appId: bigint; asaId: bigint }
    dsState: { tinymanAppId: bigint; lpId: string }
    blockRound: number
    lastRound: number
    callSwap: boolean
    txnValidity?: number
  }) {
    if (lastRound - blockRound >= 999) {
      throw new Error(`Too late - now: ${lastRound}, block: ${blockRound}, delta: ${blockRound - lastRound}`)
    }
    const firstValidRound = BigInt(blockRound + 1)
    let lastValidRound = BigInt(lastRound + txnValidity)
    if (lastValidRound - firstValidRound > 998) {
      lastValidRound = firstValidRound + BigInt(998)
    }

    const dsAppId = listing.appId
    const farmAssetId = listing.asaId

    const composer = await this.client
      .newGroup()
      .noop()
      .payout({
        args: {
          blockRound,
          callSwap,
          recipientApp: dsAppId,
        },
        staticFee: (2000).microAlgos(),
        accountReferences: [dsState.lpId],
        appReferences: [dsAppId, dsState.tinymanAppId],
        assetReferences: [farmAssetId],
        boxReferences: [algosdk.encodeUint64(dsAppId)],
        firstValidRound,
        lastValidRound,
        sender: this.sender,
        signer,
      })
      .composer()

    const { transactions: ts } = await composer.buildTransactions()
    const transactions = algosdk.assignGroupID(
      ts.map((t) => algosdk.Transaction.from_obj_for_encoding(t.get_obj_for_encoding())),
    )
    return transactions.map((t) => t.get_obj_for_encoding())
  }

  async makePayoutsTransactions({
    listing,
    dsState,
    blockRounds: br,
    lastRound,
    callSwap,

    txnValidity = TXN_VALIDITY,
  }: {
    listing: { appId: bigint; asaId: bigint }
    dsState: { tinymanAppId: bigint; lpId: string }
    blockRounds: number[]
    lastRound: number
    callSwap: boolean
    txnValidity?: number
  }) {
    const blockRounds = br.sort()
    if (blockRounds.length > 15) {
      throw new Error('Too many block rounds, max 15')
    }
    const firstBlockRound = blockRounds[0]
    const lastBlockRound = blockRounds[blockRounds.length - 1]
    if (lastRound - firstBlockRound >= 999) {
      throw new Error(`Too late - now: ${lastRound}, block: ${firstBlockRound}, delta: ${firstBlockRound - lastRound}`)
    }
    const firstValidRound = BigInt(lastBlockRound + 1)
    let lastValidRound = BigInt(lastRound + txnValidity)
    if (lastValidRound - firstValidRound > 998) {
      lastValidRound = firstValidRound + BigInt(998)
    }

    const dsAppId = listing.appId
    const farmAssetId = listing.asaId
    const fees = 1000 + blockRounds.length * 1000
    let composer = this.client.newGroup().noop()

    for (let i = 0; i < blockRounds.length; i++) {
      composer = composer.payout({
        args: {
          blockRound: blockRounds[i],
          callSwap: callSwap && i === 0,
          recipientApp: dsAppId,
        },
        staticFee: fees.microAlgos(),
        accountReferences: [dsState.lpId],
        appReferences: [dsAppId, dsState.tinymanAppId],
        assetReferences: [farmAssetId],
        boxReferences: [algosdk.encodeUint64(dsAppId)],
        firstValidRound,
        lastValidRound,
        signer,
      }) as unknown as DualstakeFarmComposer<[void | undefined]>
    }
    const { transactions: ts } = await (await composer.composer()).buildTransactions()
    const transactions = algosdk.assignGroupID(
      ts.map((t) => algosdk.Transaction.from_obj_for_encoding(t.get_obj_for_encoding())),
    )
    return transactions.map((t) => t.get_obj_for_encoding())
  }

  async getBlockProposers({ start, end, lastRound: simRound }: { start: bigint; end: bigint; lastRound: bigint }) {
    const {
      confirmations: [{ logs }],
    } = await this.client
      .newGroup()
      .logBlockProposers({
        args: {
          startRound: start!,
          endRound: end!,
        },
        firstValidRound: BigInt(Number(simRound) + 1),
        lastValidRound: BigInt(Number(simRound) + 1),
      })
      .simulate({
        round: Number(simRound),
        allowMoreLogging: true,
        allowUnnamedResources: true,
        fixSigners: true,
        allowEmptySignatures: true,
        extraOpcodeBudget: 170_000,
      })

    const props: [bigint, string][] = []
    for (let i = start; i <= end; i++) {
      const prop = algosdk.encodeAddress(logs![Number(i - start)])
      props.push([i, prop])
    }

    return props
  }
}
